import os
import re
import json
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional
from colorama import Fore, Style
from .config import AppConfig
from .models import DedupRecord, DedupLevel, DedupMatch


class Exporter:
    def __init__(self, config: AppConfig, output_dir: str = "output", verbose: bool = True):
        self.config = config
        self.verbose = verbose
        self.output_dir = output_dir
        self.encoding = config.output.get('encoding', 'utf-8-sig')
        os.makedirs(self.output_dir, exist_ok=True)

    def _log(self, msg: str, color: str = Fore.WHITE):
        if self.verbose:
            print(f"{color}{msg}{Style.RESET_ALL}")

    def _ts(self) -> str:
        fmt = self.config.output.get('date_format', '%Y%m%d_%H%M%S')
        return datetime.now().strftime(fmt)

    def _p(self, filename: str) -> str:
        base, ext = os.path.splitext(filename)
        return os.path.join(self.output_dir, f"{base}_{self._ts()}{ext}")

    def export_all(self, dedup_result: Dict) -> Dict[str, str]:
        records: Dict[Any, DedupRecord] = dedup_result['records']
        paths = {}

        paths['valid_clues'] = self.export_valid_clues(records)
        paths['all_results'] = self.export_full_results(records)
        paths['dup_log'] = self.export_duplicate_log(records)
        paths['summary'] = self.export_summary(dedup_result['summary'])

        return paths

    def export_valid_clues(self, records: Dict[Any, DedupRecord],
                           filename: str = "有效线索.xlsx") -> str:
        keep_records = [r for r in records.values() if r.keep_row]
        if not keep_records:
            self._log("⚠ 没有可导出的有效线索", Fore.YELLOW)
            return ""

        rows = []
        for r in keep_records:
            d = dict(r.original_row)
            d['_判重评分'] = round(r.score, 1)
            d['_判重等级'] = r.level.value
            d['_最终渠道'] = r.final_channel
            d['_老客复咨'] = '是' if r.is_old_customer else '否'
            d['_门店在跟'] = '是' if r.is_in_following else '否'
            rows.append(d)

        df = pd.DataFrame(rows)
        path = self._p(filename)
        df.to_excel(path, index=False)
        self._log(f"✅ 导出有效线索 [{len(keep_records)}条] → {path}", Fore.GREEN)
        return path

    def export_full_results(self, records: Dict[Any, DedupRecord],
                            filename: str = "判重全结果.xlsx") -> str:
        rows = [r.to_dict() for r in records.values()]
        df = pd.DataFrame(rows)
        path = self._p(filename)
        df.to_excel(path, index=False)
        self._log(f"✅ 导出全量结果 [{len(rows)}条] → {path}", Fore.GREEN)
        return path

    def export_duplicate_log(self, records: Dict[Any, DedupRecord],
                             filename: str = "重复明细日志.xlsx") -> str:
        rows = []
        for r in records.values():
            if not r.matched_rules:
                continue
            for m in r.matched_rules:
                rows.append({
                    '线索行号': r.row_index,
                    '判重等级': r.level.value,
                    '匹配规则': m.rule_name,
                    '相似度': round(m.score, 1),
                    '匹配字段': json.dumps(m.matched_fields, ensure_ascii=False),
                    '匹配来源': m.source_type.value,
                    '匹配对象行': m.target_index,
                    '匹配详情': m.match_detail,
                    '原始渠道': r.original_row.get('channel', ''),
                    '最终渠道': r.final_channel,
                    '是否保留': '是' if r.keep_row else '否',
                    '手机号': r.original_row.get('phone', ''),
                    '微信号': r.original_row.get('wechat', ''),
                    '姓名': r.original_row.get('name', ''),
                })

        if not rows:
            self._log("ℹ 无重复记录可导出", Fore.LIGHTBLACK_EX)
            return ""

        df = pd.DataFrame(rows)
        path = self._p(filename)
        df.to_excel(path, index=False)
        self._log(f"✅ 导出重复明细 [{len(rows)}条] → {path}", Fore.GREEN)
        return path

    def export_summary(self, summary: Dict, filename: str = "统计汇总.xlsx") -> str:
        path = self._p(filename)
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            counts = summary['counts']
            df_overall = pd.DataFrame([
                {'指标': k, '数值': v} for k, v in counts.items()
            ])
            df_overall.to_excel(writer, sheet_name='整体汇总', index=False)

            by_ch = summary['by_channel']
            ch_rows = []
            for ch, data in by_ch.items():
                row = {'渠道': ch}
                row.update(data)
                ch_rows.append(row)
            if ch_rows:
                df_ch = pd.DataFrame(ch_rows)
                df_ch.to_excel(writer, sheet_name='渠道分布', index=False)

        self._log(f"✅ 导出统计汇总 → {path}", Fore.GREEN)
        return path

    def generate_review_package(self, records: Dict[Any, DedupRecord],
                                campaign_df: pd.DataFrame = None,
                                filename: str = "人工复核包.xlsx") -> str:
        review_records = [r for r in records.values()
                          if r.level in [DedupLevel.POSSIBLE, DedupLevel.PROBABLE]
                          or (r.matched_rules and r.score >= 50)]

        if not review_records:
            self._log("ℹ 无需人工复核的记录", Fore.LIGHTBLACK_EX)
            return ""

        path = self._p(filename)
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            rows = []
            for r in review_records:
                matched_pairs = set()
                for m in r.matched_rules:
                    a = min(r.row_index, m.target_index)
                    b = max(r.row_index, m.target_index)
                    if a != b:
                        matched_pairs.add((a, b))

                for a, b in matched_pairs:
                    rec_a = records[a].original_row if a in records else {}
                    rec_b = records[b].original_row if b in records else {}
                    row = {
                        '复核组ID': f"G_{a}_{b}",
                        'A行号': a,
                        'B行号': b,
                        'A_手机号': rec_a.get('phone', ''),
                        'B_手机号': rec_b.get('phone', ''),
                        'A_微信号': rec_a.get('wechat', ''),
                        'B_微信号': rec_b.get('wechat', ''),
                        'A_姓名': rec_a.get('name', ''),
                        'B_姓名': rec_b.get('name', ''),
                        'A_生日': rec_a.get('birthday', ''),
                        'B_生日': rec_b.get('birthday', ''),
                        'A_城市': rec_a.get('city', ''),
                        'B_城市': rec_b.get('city', ''),
                        'A_意向': str(rec_a.get('intention', ''))[:50],
                        'B_意向': str(rec_b.get('intention', ''))[:50],
                        'A_渠道': rec_a.get('channel', ''),
                        'B_渠道': rec_b.get('channel', ''),
                        '最高相似度': round(r.score, 1),
                        '匹配规则': '; '.join([m.rule_name for m in r.matched_rules]),
                        '建议操作': '保留A，剔除B' if r.score >= 75 else '请人工确认',
                        '人工复核结果': '',
                        '备注': '',
                    }
                    rows.append(row)

            if rows:
                df_review = pd.DataFrame(rows)
                df_review.to_excel(writer, sheet_name='待复核', index=False)

                df_decision = pd.DataFrame([
                    {'选项': ['A为独立、B剔除', 'B为独立、A剔除', '两者都保留（不同人）', '两者都剔除（老客/在跟）',
                             '需要进一步确认']}
                ])
                df_decision.to_excel(writer, sheet_name='复核参考', index=False)

            dup_rows = []
            for r in records.values():
                if r.level == DedupLevel.DEFINITE:
                    dup_rows.append({
                        '行号': r.row_index,
                        '匹配规则': '; '.join([m.rule_name for m in r.matched_rules]),
                        '匹配对象': '; '.join([str(x) for x in r.matched_with]),
                        '相似度': round(r.score, 1),
                        '手机号': r.original_row.get('phone', ''),
                        '微信号': r.original_row.get('wechat', ''),
                        '姓名': r.original_row.get('name', ''),
                        '渠道': r.original_row.get('channel', ''),
                    })
            if dup_rows:
                pd.DataFrame(dup_rows).to_excel(writer, sheet_name='确定重复(已剔除)', index=False)

            old_rows = []
            for r in records.values():
                if r.level == DedupLevel.OLD_CUSTOMER or r.level == DedupLevel.FOLLOWING:
                    old_rows.append({
                        '行号': r.row_index,
                        '类型': r.level.value,
                        '命中规则': '; '.join([m.rule_name for m in r.matched_rules]),
                        '详情': '; '.join([m.match_detail for m in r.matched_rules[:3]]),
                        '手机号': r.original_row.get('phone', ''),
                        '姓名': r.original_row.get('name', ''),
                        '原始渠道': r.original_row.get('channel', ''),
                    })
            if old_rows:
                pd.DataFrame(old_rows).to_excel(writer, sheet_name='老客_在跟(已剔除)', index=False)

        self._log(f"✅ 生成人工复核包 → {path}", Fore.GREEN)
        return path


class BatchComparator:
    def __init__(self, config: AppConfig, verbose: bool = True):
        self.config = config
        self.verbose = verbose

    def _log(self, msg: str, color: str = Fore.WHITE):
        if self.verbose:
            print(f"{color}{msg}{Style.RESET_ALL}")

    def compare(self, result_a: Dict, result_b: Dict,
                label_a: str = "批次A", label_b: str = "批次B") -> Dict:
        recs_a = result_a['records']
        recs_b = result_b['records']

        def _key_set(records):
            keys = set()
            for r in records.values():
                row = r.original_row
                p = re.sub(r'\D', '', str(row.get('phone', '')))
                w = str(row.get('wechat', '')).strip()
                n = str(row.get('name', '')).strip()
                b = str(row.get('birthday', '')).strip()
                if p: keys.add(('P', p))
                if w: keys.add(('W', w))
                if n and b: keys.add(('NB', n + b))
            return keys

        keys_a = _key_set(recs_a)
        keys_b = _key_set(recs_b)

        common = keys_a & keys_b
        only_a = keys_a - keys_b
        only_b = keys_b - keys_a

        self._log("\n" + "=" * 60, Fore.CYAN)
        self._log(f"🔍 批次对比: {label_a} vs {label_b}", Fore.CYAN + Style.BRIGHT)
        self._log("=" * 60, Fore.CYAN)
        self._log(f"  {label_a}唯一键数: {len(keys_a)}")
        self._log(f"  {label_b}唯一键数: {len(keys_b)}")
        self._log(f"  两批共有: {len(common)}", Fore.GREEN)
        self._log(f"  仅{label_a}: {len(only_a)}", Fore.YELLOW)
        self._log(f"  仅{label_b}: {len(only_b)}", Fore.YELLOW)

        sa = result_a['summary']['counts']
        sb = result_b['summary']['counts']
        self._log(f"\n  {label_a} 有效线索: {sa.get('keep_count', 0)} (挤水率{sa.get('water_rate', 0)}%)")
        self._log(f"  {label_b} 有效线索: {sb.get('keep_count', 0)} (挤水率{sb.get('water_rate', 0)}%)")
        delta = sb.get('keep_count', 0) - sa.get('keep_count', 0)
        delta_w = round(sb.get('water_rate', 0) - sa.get('water_rate', 0), 1)
        color_d = Fore.GREEN if delta > 0 else (Fore.RED if delta < 0 else Fore.LIGHTBLACK_EX)
        self._log(f"  有效线索变化: {delta:+d} {color_d}", Fore.WHITE)
        color_w = Fore.RED if delta_w > 0 else (Fore.GREEN if delta_w < 0 else Fore.LIGHTBLACK_EX)
        self._log(f"  挤水率变化: {delta_w:+.1f}% {color_w}", Fore.WHITE)

        cha = result_a['summary']['by_channel']
        chb = result_b['summary']['by_channel']
        all_ch = set(cha.keys()) | set(chb.keys())
        self._log(f"\n  渠道明细:", Fore.CYAN)
        for ch in sorted(all_ch):
            a_val = cha.get(ch, {}).get('keep_count', cha.get(ch, {}).get('total', 0))
            b_val = chb.get(ch, {}).get('keep_count', chb.get(ch, {}).get('total', 0))
            d = b_val - a_val
            tag = f"({d:+d})" if d else ""
            color = Fore.GREEN if d > 0 else (Fore.RED if d < 0 else Fore.WHITE)
            self._log(f"    {ch}: {a_val} → {b_val} {color}{tag}", Fore.WHITE)

        self._log("=" * 60 + "\n", Fore.CYAN)

        return {
            'common_keys': list(common),
            'only_a_keys': list(only_a),
            'only_b_keys': list(only_b),
            'summary_a': sa,
            'summary_b': sb,
            'channel_diff': {
                ch: {'a': cha.get(ch, {}).get('keep_count', cha.get(ch, {}).get('total', 0)),
                     'b': chb.get(ch, {}).get('keep_count', chb.get(ch, {}).get('total', 0))}
                for ch in all_ch
            }
        }

    def export_comparison(self, cmp_result: Dict,
                          output_dir: str = "output",
                          label_a: str = "批次A", label_b: str = "批次B") -> str:
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime(self.config.output.get('date_format', '%Y%m%d_%H%M%S'))
        path = os.path.join(output_dir, f"批次对比_{ts}.xlsx")

        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            rows = []
            for ch, d in cmp_result['channel_diff'].items():
                rows.append({
                    '渠道': ch,
                    f'{label_a}_有效': d['a'],
                    f'{label_b}_有效': d['b'],
                    '变化量': d['b'] - d['a'],
                    '变化率%': round((d['b'] - d['a']) / d['a'] * 100, 1) if d['a'] else None
                })
            pd.DataFrame(rows).to_excel(writer, sheet_name='渠道对比', index=False)

            sa = cmp_result['summary_a']
            sb = cmp_result['summary_b']
            rows2 = []
            all_keys = set(sa.keys()) | set(sb.keys())
            for k in sorted(all_keys):
                rows2.append({'指标': k, label_a: sa.get(k, 0), label_b: sb.get(k, 0)})
            pd.DataFrame(rows2).to_excel(writer, sheet_name='整体对比', index=False)

            pd.DataFrame([{'唯一键类型': k[0], '值': k[1]} for k in cmp_result['only_b_keys']]
                         ).to_excel(writer, sheet_name=f'新增于{label_b}', index=False)
            pd.DataFrame([{'唯一键类型': k[0], '值': k[1]} for k in cmp_result['only_a_keys']]
                         ).to_excel(writer, sheet_name=f'消失于{label_b}', index=False)

        self._log(f"✅ 导出批次对比 → {path}", Fore.GREEN)
        return path
