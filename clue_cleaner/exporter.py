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
            d['_原始渠道'] = r.original_channel
            d['_命中渠道'] = r.hit_channel
            d['_最终归属渠道'] = r.final_channel
            linked = sorted(set(r.linked_channels) - {r.original_channel, r.final_channel}
                            if r.linked_channels else set())
            d['_关联渠道(除归属外)'] = '; '.join(linked)
            d['_全部关联渠道'] = '; '.join(sorted(set(r.linked_channels))) if r.linked_channels else r.original_channel
            d['_关联渠道数'] = len(set(r.linked_channels)) if r.linked_channels else 1
            d['_老客复咨'] = '是' if r.is_old_customer else '否'
            d['_门店在跟'] = '是' if r.is_in_following else '否'
            d['_匹配对象行号'] = '; '.join([str(x) for x in r.matched_with])
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
                    '原始渠道': r.original_channel,
                    '命中渠道': r.hit_channel,
                    '最终归属渠道': r.final_channel,
                    '全部关联渠道': '; '.join(sorted(set(r.linked_channels))) if r.linked_channels else r.original_channel,
                    '关联渠道数': len(set(r.linked_channels)) if r.linked_channels else 1,
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
            cost_col = summary.get('cost_column')
            filter_rows = []
            filters = summary.get('filters', {})
            if filters:
                for k, v in filters.items():
                    filter_rows.append({'筛选项': k, '值': v})
            filter_rows.append({'筛选项': '成本列识别', '值': cost_col or '未识别到'})
            filter_rows.append({'筛选项': '导出时间', '值': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
            pd.DataFrame(filter_rows).to_excel(writer, sheet_name='筛选条件', index=False)

            df_overall = pd.DataFrame([{'指标': k, '数值': v} for k, v in counts.items()])
            df_overall.to_excel(writer, sheet_name='整体汇总', index=False)

            by_ch = summary.get('by_channel', {})
            ch_rows = []
            for ch, data in by_ch.items():
                row = {'渠道': ch}
                row.update(data)
                ch_rows.append(row)
            if ch_rows:
                df_ch = pd.DataFrame(ch_rows)
                df_ch.to_excel(writer, sheet_name='渠道分布', index=False)

            by_orig = summary.get('by_original_channel', {})
            orig_rows = []
            for ch, data in by_orig.items():
                orig_rows.append({
                    '渠道': ch,
                    '投放量': data.get('total', 0),
                    '有效线索': data.get('keep_count', 0),
                    '剔除量': data.get('remove_count', 0),
                    '老客复咨': data.get('old_customer', 0),
                    '门店在跟': data.get('following', 0),
                    '确定重复': data.get('definite_dup', 0),
                    '待复核': data.get('review_count', 0),
                    '挤水率%': data.get('water_rate', 0),
                    '总花费': round(float(data.get('total_spent', 0)), 2),
                    '有效花费': round(float(data.get('keep_spent', 0)), 2),
                    '浪费花费': round(float(data.get('wasted_spent', 0)), 2),
                    '单条投放成本': round(float(data.get('cost_per_lead', 0)), 2),
                    '有效获客成本': round(float(data.get('effective_cost', 0)), 2),
                })
            if orig_rows:
                df_orig = pd.DataFrame(orig_rows)
                df_orig = df_orig.sort_values('投放量', ascending=False)
                df_orig.to_excel(writer, sheet_name='渠道投放明细', index=False)

        self._log(f"✅ 导出统计汇总 → {path}", Fore.GREEN)
        return path

    def generate_review_package(self, records: Dict[Any, DedupRecord],
                                campaign_df: pd.DataFrame = None,
                                filename: str = "人工复核包.xlsx") -> str:
        review_records = [r for r in records.values()
                          if r.level in [DedupLevel.POSSIBLE, DedupLevel.PROBABLE]]

        path = self._p(filename)
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            rows = []
            seen_pairs = set()
            for r in review_records:
                for m in r.matched_rules:
                    target_idx = m.target_index
                    if target_idx not in records:
                        continue
                    pair_key = tuple(sorted([r.row_index, target_idx]))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    rec_a = records[pair_key[0]]
                    rec_b = records[pair_key[1]]
                    a_row = rec_a.original_row
                    b_row = rec_b.original_row

                    row = {
                        '复核组ID': f"G_{pair_key[0]}_{pair_key[1]}",
                        'A行号': pair_key[0],
                        'B行号': pair_key[1],
                        'A_手机号': a_row.get('phone', ''),
                        'B_手机号': b_row.get('phone', ''),
                        'A_微信号': a_row.get('wechat', ''),
                        'B_微信号': b_row.get('wechat', ''),
                        'A_姓名': a_row.get('name', ''),
                        'B_姓名': b_row.get('name', ''),
                        'A_生日': a_row.get('birthday', ''),
                        'B_生日': b_row.get('birthday', ''),
                        'A_城市': a_row.get('city', ''),
                        'B_城市': b_row.get('city', ''),
                        'A_意向': str(a_row.get('intention', ''))[:50],
                        'B_意向': str(b_row.get('intention', ''))[:50],
                        'A_渠道': rec_a.original_channel,
                        'B_渠道': rec_b.original_channel,
                        'A_最终归属': rec_a.final_channel,
                        'B_最终归属': rec_b.final_channel,
                        '最高相似度': round(r.score, 1),
                        '匹配规则': '; '.join([m2.rule_name for m2 in r.matched_rules
                                               if m2.target_index in records]),
                        '建议操作': '保留高优先渠道，剔除低优先' if r.score >= 75 else '请人工确认',
                        '人工复核结果': '',
                        '备注': '',
                    }
                    rows.append(row)

            if rows:
                df_review = pd.DataFrame(rows)
                df_review.to_excel(writer, sheet_name='待复核', index=False)
            else:
                pd.DataFrame([{'说明': '无需人工复核的记录'}]).to_excel(writer, sheet_name='待复核', index=False)

            pd.DataFrame([
                {'选项': v} for v in [
                    'A为独立、B剔除', 'B为独立、A剔除', '两者都保留（不同人）',
                    '两者都剔除（老客/在跟）', '需要进一步确认'
                ]
            ]).to_excel(writer, sheet_name='复核参考', index=False)

            removed_rows = []
            for r in records.values():
                if r.level in [DedupLevel.OLD_CUSTOMER, DedupLevel.FOLLOWING, DedupLevel.DEFINITE]:
                    removed_rows.append({
                        '行号': r.row_index,
                        '剔除类型': r.level.value,
                        '匹配规则': '; '.join([m.rule_name for m in r.matched_rules]),
                        '匹配对象行': '; '.join([str(x) for x in r.matched_with]),
                        '匹配详情': '; '.join([m.match_detail for m in r.matched_rules[:3]]),
                        '手机号': r.original_row.get('phone', ''),
                        '微信号': r.original_row.get('wechat', ''),
                        '姓名': r.original_row.get('name', ''),
                        '原始渠道': r.original_channel,
                        '命中渠道': r.hit_channel,
                        '最终归属': r.final_channel,
                        '相似度': round(r.score, 1),
                    })
            if removed_rows:
                pd.DataFrame(removed_rows).to_excel(writer, sheet_name='已剔除说明', index=False)

        self._log(f"✅ 生成人工复核包 → {path}", Fore.GREEN)
        return path

    def export_weekly_report(self, dedup_result: Dict,
                             filename: str = "运营周报.xlsx",
                             json_filename: str = "运营周报.json") -> Tuple[str, str]:
        records = dedup_result['records']
        summary = dedup_result['summary']
        counts = summary['counts']
        by_orig = summary.get('by_original_channel', {})
        filters = summary.get('filters', {})
        cost_col = summary.get('cost_column')

        total = counts.get('total', 0)
        keep = counts.get('keep_count', 0)
        old_c = counts.get('老客复咨', 0)
        following = counts.get('在跟名单', 0)
        definite = counts.get('确定重复', 0)
        probable = counts.get('大概率重复', 0)
        possible = counts.get('疑似重复', 0)
        review_count = probable + possible
        total_spent = float(counts.get('total_spent', 0) or 0)
        keep_spent = float(counts.get('keep_spent', 0) or 0)
        wasted_spent = float(counts.get('wasted_spent', 0) or 0)
        eff_cost = counts.get('effective_cost', 0) or 0

        report = {
            '生成时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            '筛选条件': filters or {},
            '成本列识别': cost_col or '',
            '总投放线索': total,
            '有效线索': keep,
            '老客复咨': old_c,
            '门店在跟': following,
            '确定重复': definite,
            '待复核': review_count,
            '挤水率%': counts.get('water_rate', 0),
            '重复率%': counts.get('dup_rate', 0),
            '总花费(元)': round(total_spent, 2),
            '有效花费(元)': round(keep_spent, 2),
            '浪费花费(元)': round(wasted_spent, 2),
            '有效获客成本(元/条)': round(eff_cost, 2),
            '各渠道明细': {},
        }

        for ch, data in by_orig.items():
            report['各渠道明细'][ch] = {
                '投放量': data.get('total', 0),
                '有效线索': data.get('keep_count', 0),
                '剔除量': data.get('remove_count', 0),
                '老客复咨': data.get('old_customer', 0),
                '门店在跟': data.get('following', 0),
                '确定重复': data.get('definite_dup', 0),
                '待复核': data.get('review_count', 0),
                '挤水率%': data.get('water_rate', 0),
                '总花费(元)': round(float(data.get('total_spent', 0) or 0), 2),
                '有效花费(元)': round(float(data.get('keep_spent', 0) or 0), 2),
                '有效获客成本(元/条)': round(float(data.get('effective_cost', 0) or 0), 2),
            }

        best_by_keep = max(report['各渠道明细'].items(),
                           key=lambda x: (x[1]['有效线索'], -x[1]['挤水率%']),
                           default=(None, None))
        best_by_effcost = None
        candidates = [(ch, d) for ch, d in report['各渠道明细'].items()
                      if d['有效线索'] > 0 and d['有效获客成本(元/条)'] > 0]
        if candidates:
            best_by_effcost = min(candidates, key=lambda x: x[1]['有效获客成本(元/条)'])
        worst_by_water = max(report['各渠道明细'].items(),
                             key=lambda x: (x[1]['挤水率%'], x[1]['投放量']),
                             default=(None, None))
        worst_by_effcost = None
        if candidates:
            worst_by_effcost = max(candidates, key=lambda x: x[1]['有效获客成本(元/条)'])

        filter_desc = ''
        if filters:
            items = [f"{k}={v}" for k, v in filters.items() if v]
            if items:
                filter_desc = f"（筛选条件：{'、'.join(items)}）"

        conclusion_lines = [
            f"【运营数据复盘{filter_desc}】",
            f"  本批次共投放 {total} 条线索，挤水后保留有效 {keep} 条（挤水率 {counts.get('water_rate', 0)}%）。",
            f"  其中老客复咨 {old_c} 条、门店在跟 {following} 条、待人工复核 {review_count} 条。",
        ]
        if cost_col and total_spent > 0:
            conclusion_lines.append(
                f"  识别成本列「{cost_col}」，合计花费 ￥{total_spent:.2f}，浪费 ￥{wasted_spent:.2f}，"
                f"真实获客成本约 ￥{eff_cost:.2f}/条。"
            )
        if best_by_keep[0]:
            ch, d = best_by_keep
            conclusion_lines.append(
                f"  最佳获客渠道：{ch}（有效 {d['有效线索']} 条，挤水率 {d['挤水率%']}%）。"
            )
        if best_by_effcost:
            ch, d = best_by_effcost
            conclusion_lines.append(
                f"  最划算渠道：{ch}（有效获客成本 ￥{d['有效获客成本(元/条)']:.2f}/条）。"
            )
        if worst_by_water[0]:
            ch, d = worst_by_water
            conclusion_lines.append(
                f"  水分最高渠道：{ch}（挤水率 {d['挤水率%']}%，投放 {d['投放量']} 条），建议优化投放人群或素材。"
            )
        if worst_by_effcost and worst_by_effcost[0] != (worst_by_water[0] if worst_by_water else None):
            ch, d = worst_by_effcost
            conclusion_lines.append(
                f"  成本偏高渠道：{ch}（有效获客成本 ￥{d['有效获客成本(元/条)']:.2f}/条），可考虑降低出价或换定向。"
            )
        if review_count > 0:
            conclusion_lines.append(
                f"  ⚠ 仍有 {review_count} 条疑似重复待人工复核，建议 24 小时内完成确认后更新报表。"
            )

        report['运营结论'] = conclusion_lines
        conclusion_text = '\n'.join(conclusion_lines)

        os.makedirs(self.output_dir, exist_ok=True)
        xlsx_path = self._p(filename)
        with pd.ExcelWriter(xlsx_path, engine='openpyxl') as writer:
            pd.DataFrame([{
                '序号': i + 1,
                '运营结论': line
            } for i, line in enumerate(conclusion_lines)]).to_excel(
                writer, sheet_name='运营结论', index=False
            )

            overview_rows = []
            for k, v in report.items():
                if k in ('各渠道明细', '运营结论'):
                    continue
                overview_rows.append({'指标': k, '数值': v})
            pd.DataFrame(overview_rows).to_excel(writer, sheet_name='核心指标', index=False)

            ch_rows = []
            for ch, data in report['各渠道明细'].items():
                row = {'渠道': ch}
                row.update(data)
                ch_rows.append(row)
            if ch_rows:
                df_ch = pd.DataFrame(ch_rows)
                df_ch = df_ch.sort_values('投放量', ascending=False)
                df_ch.to_excel(writer, sheet_name='渠道挤水率', index=False)

            sorted_channels = sorted(report['各渠道明细'].items(),
                                     key=lambda x: x[1].get('挤水率%', 0), reverse=True)
            alert_rows = []
            for ch, data in sorted_channels[:5]:
                if data.get('投放量', 0) > 0:
                    alert_rows.append({
                        '渠道': ch,
                        '投放量': data['投放量'],
                        '挤水率%': data['挤水率%'],
                        '有效线索': data['有效线索'],
                        '有效获客成本(元/条)': data['有效获客成本(元/条)'],
                        '老客+在跟': data.get('老客复咨', 0) + data.get('门店在跟', 0),
                        '建议': '重点关注' if data['挤水率%'] > 20 else '正常'
                    })
            if alert_rows:
                pd.DataFrame(alert_rows).to_excel(writer, sheet_name='水分预警', index=False)

        self._log(f"✅ 导出运营周报 → {xlsx_path}", Fore.GREEN)
        self._log(f"   📝 运营结论已生成，可直接复制到日报/周会", Fore.LIGHTCYAN_EX)
        for line in conclusion_lines[:2]:
            self._log(f"   {line}", Fore.LIGHTCYAN_EX)

        json_path = os.path.join(self.output_dir, json_filename.replace('.json', f'_{self._ts()}.json'))
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        self._log(f"✅ 导出周报JSON → {json_path}", Fore.GREEN)

        return xlsx_path, json_path


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
        summary_a = result_a.get('summary', {})
        summary_b = result_b.get('summary', {})
        filters_a = summary_a.get('filters', {})
        filters_b = summary_b.get('filters', {})
        cost_a = summary_a.get('cost_column')
        cost_b = summary_b.get('cost_column')

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

        sa = summary_a['counts']
        sb = summary_b['counts']
        ocha = summary_a.get('by_original_channel', {})
        ochb = summary_b.get('by_original_channel', {})

        self._log("\n" + "=" * 70, Fore.CYAN)
        self._log(f"🔍 批次对比: {label_a} vs {label_b}", Fore.CYAN + Style.BRIGHT)
        self._log("=" * 70, Fore.CYAN)
        if filters_a:
            items_a = [f"{k}={v}" for k, v in filters_a.items() if v]
            if items_a:
                self._log(f"  {label_a}筛选: " + ", ".join(items_a), Fore.YELLOW)
        if filters_b:
            items_b = [f"{k}={v}" for k, v in filters_b.items() if v]
            if items_b:
                self._log(f"  {label_b}筛选: " + ", ".join(items_b), Fore.YELLOW)

        self._log(f"  {label_a}唯一键数: {len(keys_a)}")
        self._log(f"  {label_b}唯一键数: {len(keys_b)}")
        self._log(f"  两批共有: {len(common)}", Fore.GREEN)
        self._log(f"  仅{label_a}: {len(only_a)}", Fore.YELLOW)
        self._log(f"  仅{label_b}: {len(only_b)}", Fore.YELLOW)

        self._log(f"\n  {label_a} 有效线索: {int(sa.get('keep_count',0) or 0)} (挤水率{sa.get('water_rate', 0)}%)")
        self._log(f"  {label_b} 有效线索: {int(sb.get('keep_count',0) or 0)} (挤水率{sb.get('water_rate', 0)}%)")
        delta = int(sb.get('keep_count', 0) - sa.get('keep_count', 0))
        delta_w = round(sb.get('water_rate', 0) - sa.get('water_rate', 0), 1)
        self._log(f"  有效线索变化: {delta:+d}", Fore.GREEN if delta > 0 else Fore.RED)
        self._log(f"  挤水率变化: {delta_w:+.1f}%", Fore.RED if delta_w > 0 else Fore.GREEN)

        ta_spent = float(sa.get('total_spent', 0) or 0)
        tb_spent = float(sb.get('total_spent', 0) or 0)
        ta_keep_spent = float(sa.get('keep_spent', 0) or 0)
        tb_keep_spent = float(sb.get('keep_spent', 0) or 0)
        ta_effcost = sa.get('effective_cost', 0) or 0
        tb_effcost = sb.get('effective_cost', 0) or 0
        if (cost_a or ta_spent > 0) and (cost_b or tb_spent > 0):
            d_effcost = round(tb_effcost - ta_effcost, 2)
            self._log(f"  总花费: ￥{ta_spent:.2f} → ￥{tb_spent:.2f} ({tb_spent - ta_spent:+.2f})", Fore.LIGHTMAGENTA_EX)
            self._log(f"  有效成本: ￥{ta_effcost:.2f} → ￥{tb_effcost:.2f} ({d_effcost:+.2f})", Fore.LIGHTGREEN_EX if d_effcost < 0 else Fore.LIGHTRED_EX)

        self._log(f"\n  📊 渠道复盘明细:", Fore.CYAN)
        all_ch = set(ocha.keys()) | set(ochb.keys())
        channel_detail = {}
        for ch in sorted(all_ch):
            a_data = ocha.get(ch, {})
            b_data = ochb.get(ch, {})
            a_total = int(a_data.get('total', 0) or 0)
            b_total = int(b_data.get('total', 0) or 0)
            a_keep = int(a_data.get('keep_count', 0) or 0)
            b_keep = int(b_data.get('keep_count', 0) or 0)
            a_wr = a_data.get('water_rate', 0)
            b_wr = b_data.get('water_rate', 0)
            d_keep = int(b_keep - a_keep)
            d_wr = round(b_wr - a_wr, 1)
            a_ts = float(a_data.get('total_spent', 0) or 0)
            b_ts = float(b_data.get('total_spent', 0) or 0)
            a_ec = float(a_data.get('effective_cost', 0) or 0)
            b_ec = float(b_data.get('effective_cost', 0) or 0)
            d_spent = round(b_ts - a_ts, 2)
            d_ec = round(b_ec - a_ec, 2)
            water_tag = "↑水分变高" if d_wr > 5 else ("↓水分改善" if d_wr < -5 else "")
            real_tag = "↑获客变好" if d_keep > 0 else ("↓获客变差" if d_keep < 0 else "")
            cost_tag = ""
            if a_ec > 0 and b_ec > 0:
                if d_ec > 0:
                    cost_tag = "↑成本升高"
                elif d_ec < 0:
                    cost_tag = "↓成本降低"
            combo_tag = ""
            if d_wr < -5 and d_ec > 0:
                combo_tag = "⚠水分降成本升，注意ROI"
            elif d_wr < -5 and d_ec < 0:
                combo_tag = "✅双优：水分+成本同步改善"
            elif d_wr > 5 and d_ec > 0:
                combo_tag = "🚨双劣：水分升高+成本升高"
            elif d_keep > 0 and d_ec < 0:
                combo_tag = "💰获客变强：量增+本降"

            self._log(f"    {ch}:", Fore.CYAN)
            base_str = f"      投放 {a_total}→{b_total}  有效 {a_keep}→{b_keep}({d_keep:+d}) " \
                       f"挤水率 {a_wr}%→{b_wr}%({d_wr:+.1f}%)"
            if (a_ts > 0 or b_ts > 0):
                base_str += f"\n      花费 ￥{a_ts:.2f}→￥{b_ts:.2f}({d_spent:+.2f})  " \
                            f"有效成本 ￥{a_ec:.2f}→￥{b_ec:.2f}({d_ec:+.2f})"
            self._log(base_str, Fore.WHITE)
            if water_tag:
                self._log(f"      ⚠ {water_tag}", Fore.RED if "高" in water_tag else Fore.GREEN)
            if real_tag:
                self._log(f"      💡 {real_tag}", Fore.GREEN if "好" in real_tag else Fore.RED)
            if cost_tag:
                self._log(f"      💰 {cost_tag}", Fore.RED if "升高" in cost_tag else Fore.GREEN)
            if combo_tag:
                color = Fore.GREEN if combo_tag.startswith(("✅", "💰")) else (
                    Fore.YELLOW if combo_tag.startswith("⚠") else Fore.RED)
                self._log(f"      {combo_tag}", color + Style.BRIGHT)

            channel_detail[ch] = {
                'a_total': a_total, 'b_total': b_total,
                'a_keep': a_keep, 'b_keep': b_keep,
                'a_remove': a_data.get('remove_count', 0),
                'b_remove': b_data.get('remove_count', 0),
                'a_water_rate': a_wr, 'b_water_rate': b_wr,
                'd_wr': d_wr, 'd_keep': d_keep,
                'a_total_spent': round(a_ts, 2), 'b_total_spent': round(b_ts, 2),
                'd_spent': d_spent,
                'a_effective_cost': round(a_ec, 2), 'b_effective_cost': round(b_ec, 2),
                'd_effective_cost': d_ec,
                'water_trend': water_tag, 'real_trend': real_tag,
                'cost_trend': cost_tag, 'combo_trend': combo_tag,
                'a_old_customer': a_data.get('old_customer', 0),
                'b_old_customer': b_data.get('old_customer', 0),
                'a_following': a_data.get('following', 0),
                'b_following': b_data.get('following', 0),
                'a_review': a_data.get('review_count', 0),
                'b_review': b_data.get('review_count', 0),
            }

        self._log("=" * 70 + "\n", Fore.CYAN)

        return {
            'common_keys': list(common),
            'only_a_keys': list(only_a),
            'only_b_keys': list(only_b),
            'summary_a': sa,
            'summary_b': sb,
            'by_original_a': ocha,
            'by_original_b': ochb,
            'filters_a': filters_a,
            'filters_b': filters_b,
            'cost_column_a': cost_a,
            'cost_column_b': cost_b,
            'channel_diff': channel_detail
        }

    def export_comparison(self, cmp_result: Dict,
                          output_dir: str = "output",
                          label_a: str = "批次A", label_b: str = "批次B") -> str:
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime(self.config.output.get('date_format', '%Y%m%d_%H%M%S'))
        path = os.path.join(output_dir, f"批次对比_{ts}.xlsx")

        sa = cmp_result['summary_a']
        sb = cmp_result['summary_b']
        filters_a = cmp_result.get('filters_a', {})
        filters_b = cmp_result.get('filters_b', {})
        cost_a = cmp_result.get('cost_column_a')
        cost_b = cmp_result.get('cost_column_b')

        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            info_rows = []
            info_rows.append({'项目': f'{label_a}成本列', '值': cost_a or '未识别'})
            info_rows.append({'项目': f'{label_b}成本列', '值': cost_b or '未识别'})
            for k, v in filters_a.items():
                info_rows.append({'项目': f'{label_a}_{k}', '值': v})
            for k, v in filters_b.items():
                info_rows.append({'项目': f'{label_b}_{k}', '值': v})
            info_rows.append({'项目': '导出时间', '值': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
            pd.DataFrame(info_rows).to_excel(writer, sheet_name='筛选条件_说明', index=False)

            rows = []
            for ch in sorted(cmp_result['channel_diff'].keys()):
                d = cmp_result['channel_diff'][ch]
                rows.append({
                    '渠道': ch,
                    f'{label_a}_投放量': d['a_total'],
                    f'{label_b}_投放量': d['b_total'],
                    f'{label_a}_有效线索': d['a_keep'],
                    f'{label_b}_有效线索': d['b_keep'],
                    '有效线索变化': d['d_keep'],
                    f'{label_a}_剔除量': d['a_remove'],
                    f'{label_b}_剔除量': d['b_remove'],
                    f'{label_a}_挤水率%': d['a_water_rate'],
                    f'{label_b}_挤水率%': d['b_water_rate'],
                    '挤水率变化': d['d_wr'],
                    f'{label_a}_总花费': d['a_total_spent'],
                    f'{label_b}_总花费': d['b_total_spent'],
                    '花费变化': d['d_spent'],
                    f'{label_a}_有效获客成本': d['a_effective_cost'],
                    f'{label_b}_有效获客成本': d['b_effective_cost'],
                    '有效成本变化': d['d_effective_cost'],
                    '水分趋势': d['water_trend'] or '持平',
                    '获客趋势': d['real_trend'] or '持平',
                    '成本趋势': d['cost_trend'] or '持平',
                    '综合结论': d['combo_trend'] or '-',
                })
            pd.DataFrame(rows).to_excel(writer, sheet_name='渠道复盘', index=False)

            rows2 = []
            all_keys = set(sa.keys()) | set(sb.keys())
            for k in sorted(all_keys):
                va = sa.get(k, 0)
                vb = sb.get(k, 0)
                try:
                    change = round(vb - va, 2)
                except Exception:
                    change = '-'
                rows2.append({'指标': k, label_a: va, label_b: vb, '变化': change})
            pd.DataFrame(rows2).to_excel(writer, sheet_name='整体对比', index=False)

            if cmp_result.get('only_b_keys'):
                pd.DataFrame([{'唯一键类型': k[0], '值': k[1]} for k in cmp_result['only_b_keys']]
                             ).to_excel(writer, sheet_name=f'新增于{label_b}', index=False)
            if cmp_result.get('only_a_keys'):
                pd.DataFrame([{'唯一键类型': k[0], '值': k[1]} for k in cmp_result['only_a_keys']]
                             ).to_excel(writer, sheet_name=f'消失于{label_b}', index=False)

        self._log(f"✅ 导出批次对比 → {path}", Fore.GREEN)
        return path

    def export_compare_report(self, cmp_result: Dict,
                              output_dir: str = "output",
                              label_a: str = "批次A", label_b: str = "批次B") -> str:
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime(self.config.output.get('date_format', '%Y%m%d_%H%M%S'))
        path = os.path.join(output_dir, f"对比周报_{ts}.xlsx")

        sa = cmp_result['summary_a']
        sb = cmp_result['summary_b']
        filters_a = cmp_result.get('filters_a', {})
        filters_b = cmp_result.get('filters_b', {})
        cost_a = cmp_result.get('cost_column_a')
        cost_b = cmp_result.get('cost_column_b')

        ta_total = sa.get('total', 0)
        tb_total = sb.get('total', 0)
        ta_keep = sa.get('keep_count', 0)
        tb_keep = sb.get('keep_count', 0)
        ta_wr = sa.get('water_rate', 0)
        tb_wr = sb.get('water_rate', 0)
        ta_old = sa.get('老客复咨', 0)
        tb_old = sb.get('老客复咨', 0)
        ta_flw = sa.get('在跟名单', 0)
        tb_flw = sb.get('在跟名单', 0)
        ta_review = sa.get('大概率重复', 0) + sa.get('疑似重复', 0)
        tb_review = sb.get('大概率重复', 0) + sb.get('疑似重复', 0)
        ta_spent = float(sa.get('total_spent', 0) or 0)
        tb_spent = float(sb.get('total_spent', 0) or 0)
        ta_ec = sa.get('effective_cost', 0) or 0
        tb_ec = sb.get('effective_cost', 0) or 0

        fa_desc = []
        fb_desc = []
        if filters_a:
            fa_desc = [f"{k}={v}" for k, v in filters_a.items() if v]
        if filters_b:
            fb_desc = [f"{k}={v}" for k, v in filters_b.items() if v]

        conclusion_lines = [
            f"【批次对比复盘：{label_a} vs {label_b}】",
            f"  投放规模：{label_a} {ta_total}条 → {label_b} {tb_total}条 "
            f"({'增' if tb_total > ta_total else '减' if tb_total < ta_total else '持平'}"
            f" {abs(tb_total - ta_total)}条)",
            f"  有效线索：{label_a} {ta_keep}条（{ta_wr}%挤水率）→ "
            f"{label_b} {tb_keep}条（{tb_wr}%挤水率），"
            f"有效线索{tb_keep - ta_keep:+d}条，挤水率{tb_wr - ta_wr:+.1f}%。",
            f"  老客复咨：{ta_old} → {tb_old}，门店在跟：{ta_flw} → {tb_flw}，待复核：{ta_review} → {tb_review}。",
        ]
        if cost_a or cost_b or ta_spent > 0 or tb_spent > 0:
            conclusion_lines.append(
                f"  花费识别：{label_a}「{cost_a or '未识别'}」/ {label_b}「{cost_b or '未识别'}」，"
                f"总花费 ￥{ta_spent:.2f} → ￥{tb_spent:.2f}({tb_spent - ta_spent:+.2f})。"
            )
        if (ta_ec > 0 or tb_ec > 0):
            d_ec = round(tb_ec - ta_ec, 2)
            conclusion_lines.append(
                f"  有效获客成本：￥{ta_ec:.2f} → ￥{tb_ec:.2f}({d_ec:+.2f})，"
                + ("成本更划算了。" if d_ec < 0 else "成本上升需关注。")
            )

        ch_items = list(cmp_result['channel_diff'].items())
        if ch_items:
            best_keep = max(ch_items, key=lambda x: x[1]['d_keep'])
            if best_keep[1]['d_keep'] != 0:
                d = best_keep[1]
                conclusion_lines.append(
                    f"  获客增长最多：{best_keep[0]}（有效{d['d_keep']:+d}条，"
                    f"挤水率变化{d['d_wr']:+.1f}%）。"
                )
            ec_cand = [x for x in ch_items
                       if x[1]['a_effective_cost'] > 0 and x[1]['b_effective_cost'] > 0
                       and x[1]['b_keep'] > 0]
            if ec_cand:
                best_ec = min(ec_cand, key=lambda x: x[1]['d_effective_cost'])
                d = best_ec[1]
                if d['d_effective_cost'] != 0:
                    conclusion_lines.append(
                        f"  成本改善最多：{best_ec[0]}（有效成本{d['d_effective_cost']:+.2f}元/条）。"
                    )
            worst_wr = max(ch_items, key=lambda x: x[1]['d_wr'])
            if worst_wr[1]['d_wr'] > 5:
                d = worst_wr[1]
                conclusion_lines.append(
                    f"  ⚠ 渠道风险预警：{worst_wr[0]} 挤水率显著上升{d['d_wr']:+.1f}%，建议排查流量质量。"
                )
            bad_combo = [x for x in ch_items if '双劣' in x[1].get('combo_trend', '')]
            if bad_combo:
                names = '、'.join([x[0] for x in bad_combo])
                conclusion_lines.append(f"  🚨 双劣警告（水升+本升）：{names}，需重点优化。")

        if fa_desc or fb_desc:
            desc_parts = []
            if fa_desc:
                desc_parts.append(f"{label_a}：{'/'.join(fa_desc)}")
            if fb_desc:
                desc_parts.append(f"{label_b}：{'/'.join(fb_desc)}")
            conclusion_lines.append(f"  【注】本次对比筛选条件：{'；'.join(desc_parts)}。")

        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            pd.DataFrame([{
                '序号': i + 1,
                '对比结论（可直接复制）': line
            } for i, line in enumerate(conclusion_lines)]).to_excel(
                writer, sheet_name='运营结论', index=False
            )

            info_rows = []
            info_rows.append({'项目': f'{label_a}成本列', '值': cost_a or '未识别'})
            info_rows.append({'项目': f'{label_b}成本列', '值': cost_b or '未识别'})
            for k, v in filters_a.items():
                info_rows.append({'项目': f'{label_a}_{k}', '值': v})
            for k, v in filters_b.items():
                info_rows.append({'项目': f'{label_b}_{k}', '值': v})
            info_rows.append({'项目': '导出时间', '值': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
            pd.DataFrame(info_rows).to_excel(writer, sheet_name='筛选条件_说明', index=False)

            overview = [
                {'指标': '总投放', label_a: ta_total, label_b: tb_total},
                {'指标': '有效线索', label_a: ta_keep, label_b: tb_keep},
                {'指标': '老客复咨', label_a: ta_old, label_b: tb_old},
                {'指标': '门店在跟', label_a: ta_flw, label_b: tb_flw},
                {'指标': '待复核', label_a: ta_review, label_b: tb_review},
                {'指标': '挤水率%', label_a: ta_wr, label_b: tb_wr},
                {'指标': '总花费(元)', label_a: round(ta_spent, 2), label_b: round(tb_spent, 2)},
                {'指标': '有效获客成本(元/条)', label_a: round(ta_ec, 2), label_b: round(tb_ec, 2)},
            ]
            pd.DataFrame(overview).to_excel(writer, sheet_name='核心指标对比', index=False)

            ch_rows = []
            for ch in sorted(cmp_result['channel_diff'].keys()):
                d = cmp_result['channel_diff'][ch]
                ch_rows.append({
                    '渠道': ch,
                    f'{label_a}_投放': d['a_total'],
                    f'{label_b}_投放': d['b_total'],
                    f'{label_a}_有效': d['a_keep'],
                    f'{label_b}_有效': d['b_keep'],
                    '有效变化': d['d_keep'],
                    f'{label_a}_挤水率%': d['a_water_rate'],
                    f'{label_b}_挤水率%': d['b_water_rate'],
                    '挤水率变化': d['d_wr'],
                    f'{label_a}_总花费': d['a_total_spent'],
                    f'{label_b}_总花费': d['b_total_spent'],
                    f'{label_a}_有效成本': d['a_effective_cost'],
                    f'{label_b}_有效成本': d['b_effective_cost'],
                    '成本变化': d['d_effective_cost'],
                    '水分趋势': d['water_trend'] or '持平',
                    '获客趋势': d['real_trend'] or '持平',
                    '成本趋势': d['cost_trend'] or '持平',
                    '综合结论': d['combo_trend'] or '-',
                })
            if ch_rows:
                pd.DataFrame(ch_rows).to_excel(writer, sheet_name='渠道挤水率对比', index=False)

        self._log(f"✅ 导出对比周报 → {path}", Fore.GREEN)
        self._log(f"   📝 对比结论已生成，可直接复制到日报", Fore.LIGHTCYAN_EX)
        for line in conclusion_lines[:3]:
            self._log(f"   {line}", Fore.LIGHTCYAN_EX)
        return path
