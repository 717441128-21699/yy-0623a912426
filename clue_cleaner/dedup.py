import re
import pandas as pd
from typing import List, Dict, Tuple, Optional, Any
from collections import defaultdict
from colorama import Fore, Style
from rapidfuzz import fuzz
from tqdm import tqdm
from .config import AppConfig
from .models import (
    DedupRecord, DedupMatch, DedupLevel, SourceType,
    clean_phone, clean_wechat, clean_name, clean_city, clean_intention
)


class DedupEngine:
    def __init__(self, config: AppConfig, verbose: bool = True):
        self.config = config
        self.verbose = verbose
        self._build_indexes_called = False

    def _log(self, msg: str, color: str = Fore.WHITE):
        if self.verbose:
            print(f"{color}{msg}{Style.RESET_ALL}")

    def run(self,
            campaign_df: pd.DataFrame,
            history_df: pd.DataFrame = None,
            following_df: pd.DataFrame = None) -> Dict[str, Any]:
        if campaign_df is None or campaign_df.empty:
            raise ValueError("本次投放数据不能为空")

        self._log("\n🚀 启动判重引擎...", Fore.CYAN + Style.BRIGHT)

        records: Dict[Any, DedupRecord] = {}
        for idx in campaign_df.index:
            row = campaign_df.loc[idx].to_dict()
            records[idx] = DedupRecord(
                row_index=idx,
                original_row=row,
                final_channel=str(row.get('channel', row.get('_source_label', '未知')))
            )

        self._log(f"📥 加载投放线索: {len(records)} 条", Fore.CYAN)

        if history_df is not None and not history_df.empty:
            self._log(f"📜 历史成交库: {len(history_df)} 条", Fore.CYAN)
            self._match_history(records, history_df)

        if following_df is not None and not following_df.empty:
            self._log(f"👥 门店在跟库: {len(following_df)} 条", Fore.CYAN)
            self._match_following(records, following_df)

        self._match_internal(records, campaign_df)
        self._apply_scoring_and_level(records)
        self._resolve_channel_priority(records)
        self._decide_keep_or_remove(records)

        summary = self._generate_summary(records)
        self._print_summary(summary)

        return {
            'records': records,
            'summary': summary,
            'campaign_df': campaign_df,
            'history_df': history_df,
            'following_df': following_df
        }

    def _build_phone_index(self, df: pd.DataFrame) -> Dict[str, List[Any]]:
        idx = defaultdict(list)
        phone_col = '_phone_norm' if '_phone_norm' in df.columns else 'phone'
        for i, row in df.iterrows():
            p = clean_phone(row.get(phone_col, ''))
            if p:
                idx[p].append(i)
        return dict(idx)

    def _build_wechat_index(self, df: pd.DataFrame) -> Dict[str, List[Any]]:
        idx = defaultdict(list)
        wx_col = '_wechat_norm' if '_wechat_norm' in df.columns else 'wechat'
        for i, row in df.iterrows():
            w = clean_wechat(row.get(wx_col, ''))
            if w:
                idx[w].append(i)
        return dict(idx)

    def _get_norm_vals(self, row: Dict, fields: List[str]) -> Dict[str, str]:
        result = {}
        norm_map = {
            'phone': '_phone_norm',
            'wechat': '_wechat_norm',
            'name': '_name_norm',
            'birthday': '_birthday_norm',
            'city': '_city_norm',
            'intention': '_intention_norm'
        }
        for f in fields:
            norm_col = norm_map.get(f, f)
            v = row.get(norm_col) or row.get(f)
            if f == 'phone':
                v = clean_phone(v)
            elif f == 'wechat':
                v = clean_wechat(v)
            elif f == 'name':
                v = clean_name(v)
            elif f == 'city':
                v = clean_city(v)
            elif f == 'intention':
                v = clean_intention(v)
            result[f] = str(v) if v is not None else ""
        return result

    def _match_history(self, records: Dict[Any, DedupRecord], history_df: pd.DataFrame):
        self._log("  → 匹配历史成交客户库...", Fore.MAGENTA)

        phone_idx = self._build_phone_index(history_df)
        wx_idx = self._build_wechat_index(history_df)

        rule_cfg = self.config.dedup_rules

        for idx, rec in tqdm(records.items(), disable=not self.verbose,
                             desc="    老客匹配", unit="条"):
            row = rec.original_row
            vals = self._get_norm_vals(row, ['phone', 'wechat', 'name', 'birthday'])

            phone = vals['phone']
            if phone and phone in phone_idx:
                for h_idx in phone_idx[phone]:
                    h_vals = self._get_norm_vals(history_df.loc[h_idx].to_dict(), ['phone', 'name', 'birthday'])
                    score = self._calc_score(
                        vals, h_vals,
                        rule_cfg.get('phone_exact', {'weight': 100}),
                        ['phone']
                    )
                    self._add_match(rec, DedupMatch(
                        rule_name="手机号匹配-历史成交",
                        matched_fields={'phone': phone},
                        score=score,
                        target_index=h_idx,
                        source_index=idx,
                        source_type=SourceType.HISTORY,
                        match_detail=f"命中历史成交: {history_df.loc[h_idx].get('name', '')} {history_df.loc[h_idx].get('amount', '')}"
                    ))
                    rec.is_old_customer = True

            wx = vals['wechat']
            if wx and wx in wx_idx:
                for h_idx in wx_idx[wx]:
                    h_vals = self._get_norm_vals(history_df.loc[h_idx].to_dict(), ['wechat', 'name', 'birthday'])
                    score = self._calc_score(
                        vals, h_vals,
                        rule_cfg.get('wechat_exact', {'weight': 90}),
                        ['wechat']
                    )
                    self._add_match(rec, DedupMatch(
                        rule_name="微信号匹配-历史成交",
                        matched_fields={'wechat': wx},
                        score=score,
                        target_index=h_idx,
                        source_index=idx,
                        source_type=SourceType.HISTORY,
                        match_detail=f"命中历史成交: {history_df.loc[h_idx].get('name', '')}"
                    ))
                    rec.is_old_customer = True

            nb = vals['name'] + vals['birthday']
            if vals['name'] and vals['birthday'] and len(vals['name']) >= 2:
                for h_idx, h_row in history_df.iterrows():
                    h_vals = self._get_norm_vals(h_row.to_dict(), ['name', 'birthday'])
                    if vals['name'] == h_vals['name'] and vals['birthday'] and h_vals['birthday'] and vals['birthday'] == h_vals['birthday']:
                        score = rule_cfg.get('name_birthday', {}).get('weight', 85)
                        self._add_match(rec, DedupMatch(
                            rule_name="姓名生日匹配-历史成交",
                            matched_fields={'name': vals['name'], 'birthday': vals['birthday']},
                            score=score,
                            target_index=h_idx,
                            source_index=idx,
                            source_type=SourceType.HISTORY,
                            match_detail=f"命中历史成交: {h_row.get('channel', '')} {h_row.get('amount', '')}"
                        ))
                        rec.is_old_customer = True
                        break

    def _match_following(self, records: Dict[Any, DedupRecord], following_df: pd.DataFrame):
        self._log("  → 匹配门店在跟名单...", Fore.MAGENTA)

        phone_idx = self._build_phone_index(following_df)
        wx_idx = self._build_wechat_index(following_df)

        for idx, rec in tqdm(records.items(), disable=not self.verbose,
                             desc="    在跟匹配", unit="条"):
            if rec.is_old_customer:
                continue

            row = rec.original_row
            vals = self._get_norm_vals(row, ['phone', 'wechat', 'name'])

            phone = vals['phone']
            if phone and phone in phone_idx:
                for f_idx in phone_idx[phone]:
                    f_row = following_df.loc[f_idx]
                    score = self.config.dedup_rules.get('phone_exact', {}).get('weight', 100)
                    self._add_match(rec, DedupMatch(
                        rule_name="手机号匹配-门店在跟",
                        matched_fields={'phone': phone},
                        score=score,
                        target_index=f_idx,
                        source_index=idx,
                        source_type=SourceType.FOLLOWING,
                        match_detail=f"命中在跟: {f_row.get('consultant', '')} {f_row.get('status', '')}"
                    ))
                    rec.is_in_following = True

            wx = vals['wechat']
            if wx and wx in wx_idx:
                for f_idx in wx_idx[wx]:
                    f_row = following_df.loc[f_idx]
                    score = self.config.dedup_rules.get('wechat_exact', {}).get('weight', 90)
                    self._add_match(rec, DedupMatch(
                        rule_name="微信号匹配-门店在跟",
                        matched_fields={'wechat': wx},
                        score=score,
                        target_index=f_idx,
                        source_index=idx,
                        source_type=SourceType.FOLLOWING,
                        match_detail=f"命中在跟: {f_row.get('consultant', '')}"
                    ))
                    rec.is_in_following = True

    def _match_internal(self, records: Dict[Any, DedupRecord], campaign_df: pd.DataFrame):
        self._log("  → 投放数据内部判重...", Fore.MAGENTA)

        rule_cfg = self.config.dedup_rules
        idxs = list(records.keys())

        phone_idx = defaultdict(list)
        wx_idx = defaultdict(list)
        nb_idx = defaultdict(list)

        for i in idxs:
            vals = self._get_norm_vals(records[i].original_row, ['phone', 'wechat', 'name', 'birthday', 'city', 'intention'])
            if vals['phone']:
                phone_idx[vals['phone']].append(i)
            if vals['wechat']:
                wx_idx[vals['wechat']].append(i)
            nb_key = vals['name'] + "|" + vals['birthday']
            if vals['name'] and vals['birthday']:
                nb_idx[nb_key].append(i)

        for phone, matched_ids in phone_idx.items():
            if len(matched_ids) > 1:
                self._mark_group_duplicates(records, matched_ids, "手机号精确匹配", 'phone', phone,
                                            rule_cfg.get('phone_exact', {}).get('weight', 100))

        for wx, matched_ids in wx_idx.items():
            if len(matched_ids) > 1:
                self._mark_group_duplicates(records, matched_ids, "微信号精确匹配", 'wechat', wx,
                                            rule_cfg.get('wechat_exact', {}).get('weight', 90))

        for nb, matched_ids in nb_idx.items():
            if len(matched_ids) > 1:
                self._mark_group_duplicates(records, matched_ids, "姓名+生日匹配", 'name_birthday', nb,
                                            rule_cfg.get('name_birthday', {}).get('weight', 85))

        nci_rule = rule_cfg.get('name_city_intention', {})
        if nci_rule.get('enabled', True):
            self._match_nci_fuzzy(records, campaign_df, nci_rule)

        npf_rule = rule_cfg.get('name_phone_fuzzy', {})
        if npf_rule.get('enabled', True):
            self._match_name_phone_fuzzy(records, campaign_df, npf_rule)

    def _mark_group_duplicates(self, records: Dict[Any, DedupRecord],
                               ids: List[Any], rule_name: str,
                               field_key: str, field_val: str, weight: int):
        ids_sorted = sorted(ids, key=lambda x: records[x].row_index)
        keep_id = ids_sorted[0]

        for dup_id in ids_sorted[1:]:
            rec = records[dup_id]
            match = DedupMatch(
                rule_name=rule_name,
                matched_fields={field_key: field_val},
                score=weight,
                target_index=keep_id,
                source_index=dup_id,
                source_type=SourceType.CAMPAIGN,
                match_detail=f"与行{keep_id}重复"
            )
            self._add_match(rec, match)
            rec.matched_with.append(keep_id)

    def _match_nci_fuzzy(self, records: Dict[Any, DedupRecord],
                         df: pd.DataFrame, rule_cfg: Dict):
        threshold = rule_cfg.get('threshold', 85)
        weight = rule_cfg.get('weight', 70)
        idxs = list(records.keys())

        buckets = defaultdict(list)
        for i in idxs:
            vals = self._get_norm_vals(records[i].original_row, ['city', 'name', 'intention'])
            if vals['city']:
                buckets[vals['city']].append((i, vals))

        for city, items in tqdm(buckets.items(), disable=not self.verbose,
                                desc="    姓名城市意向", unit="组"):
            if len(items) < 2:
                continue
            for ia in range(len(items)):
                for ib in range(ia + 1, len(items)):
                    id_a, v_a = items[ia]
                    id_b, v_b = items[ib]

                    if not v_a['name'] or not v_b['name']:
                        continue

                    name_ratio = fuzz.ratio(v_a['name'], v_b['name'])

                    intent_ratio = 100
                    if v_a['intention'] and v_b['intention']:
                        intent_ratio = fuzz.partial_ratio(v_a['intention'], v_b['intention'])
                    elif v_a['intention'] or v_b['intention']:
                        intent_ratio = 50

                    combo = name_ratio * 0.6 + intent_ratio * 0.4

                    if combo >= threshold:
                        keep = min(id_a, id_b)
                        dup = max(id_a, id_b)
                        rec = records[dup]
                        self._add_match(rec, DedupMatch(
                            rule_name="姓名+城市+意向模糊匹配",
                            matched_fields={'name': f"{v_a['name']}~{v_b['name']}",
                                            'city': city,
                                            'intention': f"{v_a['intention'][:20]}~{v_b['intention'][:20]}"},
                            score=weight * (combo / 100.0),
                            target_index=keep,
                            source_index=dup,
                            source_type=SourceType.CAMPAIGN,
                            match_detail=f"综合相似度{combo:.0f}%"
                        ))
                        rec.matched_with.append(keep)

    def _match_name_phone_fuzzy(self, records: Dict[Any, DedupRecord],
                                df: pd.DataFrame, rule_cfg: Dict):
        threshold = rule_cfg.get('threshold', 90)
        weight = rule_cfg.get('weight', 80)
        idxs = list(records.keys())

        phone7_buckets = defaultdict(list)
        for i in idxs:
            vals = self._get_norm_vals(records[i].original_row, ['phone', 'name'])
            if len(vals['phone']) >= 10:
                phone7_buckets[vals['phone'][-7:]].append((i, vals))

        for suffix, items in phone7_buckets.items():
            if len(items) < 2:
                continue
            for ia in range(len(items)):
                for ib in range(ia + 1, len(items)):
                    id_a, v_a = items[ia]
                    id_b, v_b = items[ib]
                    if not v_a['name'] or not v_b['name']:
                        continue
                    name_ratio = fuzz.ratio(v_a['name'], v_b['name'])
                    if name_ratio >= threshold * 0.9:
                        keep = min(id_a, id_b)
                        dup = max(id_a, id_b)
                        rec = records[dup]
                        self._add_match(rec, DedupMatch(
                            rule_name="姓名+手机尾号匹配",
                            matched_fields={'phone_suffix': suffix,
                                            'name': f"{v_a['name']}~{v_b['name']}"},
                            score=weight * (name_ratio / 100.0),
                            target_index=keep,
                            source_index=dup,
                            source_type=SourceType.CAMPAIGN,
                            match_detail=f"姓名相似度{name_ratio:.0f}%"
                        ))
                        rec.matched_with.append(keep)

    def _add_match(self, record: DedupRecord, match: DedupMatch):
        record.matched_rules.append(match)
        if match.score > record.score:
            record.score = match.score

    def _calc_score(self, vals_a: Dict, vals_b: Dict,
                    rule_cfg: Dict, match_fields: List[str]) -> float:
        weight = rule_cfg.get('weight', 80)
        threshold = rule_cfg.get('threshold', 85)
        fuzzy_fields = rule_cfg.get('fuzzy_fields', [])

        score = 0
        matched_count = 0
        for f in match_fields:
            va = vals_a.get(f, '')
            vb = vals_b.get(f, '')
            if not va or not vb:
                continue
            matched_count += 1
            if f in fuzzy_fields:
                score += fuzz.ratio(va, vb)
            else:
                score += 100 if va == vb else 0

        if matched_count == 0:
            return 0
        avg_score = score / matched_count
        if avg_score >= threshold:
            return weight * (avg_score / 100.0)
        return min(weight * 0.3, avg_score * weight / 100)

    def _apply_scoring_and_level(self, records: Dict[Any, DedupRecord]):
        th = self.config.score_thresholds

        for rec in records.values():
            if rec.is_old_customer:
                rec.level = DedupLevel.OLD_CUSTOMER
            elif rec.is_in_following:
                rec.level = DedupLevel.FOLLOWING
            elif not rec.matched_rules:
                rec.level = DedupLevel.UNIQUE
            else:
                s = rec.score
                if s >= th['definite_dup']:
                    rec.level = DedupLevel.DEFINITE
                elif s >= th['probable_dup']:
                    rec.level = DedupLevel.PROBABLE
                elif s >= th['possible_dup']:
                    rec.level = DedupLevel.POSSIBLE
                else:
                    rec.level = DedupLevel.UNIQUE

    def _resolve_channel_priority(self, records: Dict[Any, DedupRecord]):
        for rec in records.values():
            candidates = [rec.final_channel]
            for m in rec.matched_rules:
                if m.source_type == SourceType.HISTORY:
                    candidates.append("历史成交")
                elif m.source_type == SourceType.FOLLOWING:
                    candidates.append("门店在跟")

            best_channel = min(candidates, key=lambda c: self.config.get_channel_priority(c))
            rec.final_channel = best_channel

    def _decide_keep_or_remove(self, records: Dict[Any, DedupRecord]):
        for rec in records.values():
            if rec.level in [DedupLevel.OLD_CUSTOMER, DedupLevel.FOLLOWING]:
                rec.keep_row = False
            elif rec.level in [DedupLevel.DEFINITE]:
                rec.keep_row = False
            elif rec.level in [DedupLevel.PROBABLE, DedupLevel.POSSIBLE]:
                rec.keep_row = True
            else:
                rec.keep_row = True

    def _generate_summary(self, records: Dict[Any, DedupRecord]) -> Dict:
        summary = defaultdict(int)
        by_channel = defaultdict(lambda: defaultdict(int))

        for rec in records.values():
            summary[rec.level.value] += 1
            summary['total'] += 1
            by_channel[rec.final_channel][rec.level.value] += 1
            by_channel[rec.final_channel]['total'] += 1
            if rec.keep_row:
                summary['keep_count'] += 1
            if rec.is_old_customer:
                summary['old_customer'] += 1
            if rec.is_in_following:
                summary['following'] += 1

        summary['dup_count'] = summary['total'] - summary['keep_count']
        if summary['total'] > 0:
            summary['dup_rate'] = round(summary['dup_count'] / summary['total'] * 100, 1)
            summary['water_rate'] = round(
                (summary.get('确定重复', 0) + summary.get('老客复咨', 0) + summary.get('在跟名单', 0))
                / summary['total'] * 100, 1
            )

        return {
            'counts': dict(summary),
            'by_channel': {k: dict(v) for k, v in by_channel.items()}
        }

    def _print_summary(self, summary: Dict):
        counts = summary['counts']
        self._log("\n" + "=" * 60, Fore.CYAN)
        self._log("🎯 判重结果汇总", Fore.CYAN + Style.BRIGHT)
        self._log("=" * 60, Fore.CYAN)
        self._log(f"  总线索数:     {counts.get('total', 0)}")
        self._log(f"  独立线索:     {counts.get('独立线索', 0)}", Fore.GREEN)
        self._log(f"  确定重复:     {counts.get('确定重复', 0)}", Fore.RED)
        self._log(f"  大概率重复:   {counts.get('大概率重复', 0)}", Fore.LIGHTRED_EX)
        self._log(f"  疑似重复:     {counts.get('疑似重复', 0)}", Fore.YELLOW)
        self._log(f"  老客复咨:     {counts.get('老客复咨', 0)}", Fore.MAGENTA)
        self._log(f"  门店在跟:     {counts.get('在跟名单', 0)}", Fore.BLUE)
        self._log("-" * 60, Fore.CYAN)
        self._log(f"  保留线索数:   {counts.get('keep_count', 0)}", Fore.GREEN + Style.BRIGHT)
        self._log(f"  剔除总数:     {counts.get('dup_count', 0)}")
        self._log(f"  重复率:       {counts.get('dup_rate', 0)}%")
        self._log(f"  挤水率:       {counts.get('water_rate', 0)}%", Fore.RED + Style.BRIGHT)
        self._log("=" * 60 + "\n", Fore.CYAN)
