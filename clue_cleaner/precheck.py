import re
import pandas as pd
from typing import List, Dict, Tuple, Any
from colorama import Fore, Style
from .config import AppConfig
from .models import (
    PrecheckResult, FieldMapping,
    validate_phone, validate_wechat, clean_phone, clean_wechat
)


class PrecheckEngine:
    def __init__(self, config: AppConfig, verbose: bool = True):
        self.config = config
        self.verbose = verbose

    def _log(self, msg: str, color: str = Fore.WHITE):
        if self.verbose:
            print(f"{color}{msg}{Style.RESET_ALL}")

    def run(self, df: pd.DataFrame,
            field_mappings: List[FieldMapping] = None) -> PrecheckResult:
        result = PrecheckResult()
        result.total_rows = len(df)
        result.field_mappings = field_mappings or []

        if df.empty:
            result.warnings.append("数据为空")
            return result

        self._check_field_coverage(df, result)
        self._check_phone_column(df, result)
        self._check_wechat_column(df, result)
        self._check_empty_rows(df, result)
        self._check_internal_duplicates(df, result)
        self._compute_field_stats(df, result)

        return result

    def _check_field_coverage(self, df: pd.DataFrame, result: PrecheckResult):
        required = ['phone', 'name']
        optional = ['wechat', 'birthday', 'city', 'intention', 'channel']

        for field in required:
            if field in df.columns:
                non_null = df[field].notna().sum()
                self._log(f"  ✓ 核心字段 [{field}]: 非空 {non_null}/{len(df)}", Fore.GREEN)
            else:
                msg = f"缺失核心字段: {field}"
                result.warnings.append(msg)
                self._log(f"  ✗ {msg}", Fore.RED)

        for field in optional:
            if field in df.columns:
                non_null = df[field].notna().sum()
                pct = non_null / len(df) * 100
                tag = "✓" if pct > 50 else "~"
                color = Fore.GREEN if pct > 50 else Fore.YELLOW
                self._log(f"  {tag} 可选字段 [{field}]: 非空 {non_null}/{len(df)} ({pct:.1f}%)", color)
            else:
                self._log(f"  - 可选字段 [{field}]: 未提供", Fore.LIGHTBLACK_EX)

    def _check_phone_column(self, df: pd.DataFrame, result: PrecheckResult):
        if 'phone' not in df.columns:
            result.missing_phone_count = len(df)
            result.warnings.append("完全缺失手机号列")
            return

        phone_col = df['phone']
        phone_norm = phone_col.apply(clean_phone)
        empty_mask = phone_norm == ""

        result.missing_phone_count = int(empty_mask.sum())
        result.missing_phone_rows = df.index[empty_mask].tolist()

        invalid_mask = ~empty_mask & ~phone_norm.apply(
            lambda x: validate_phone(x, self.config.phone_regex)
        )
        result.invalid_phone_count = int(invalid_mask.sum())
        result.invalid_phone_rows = df.index[invalid_mask].tolist()

        total_bad = result.missing_phone_count + result.invalid_phone_count
        self._log(f"📱 手机号检查:", Fore.CYAN)
        if result.missing_phone_count > 0:
            self._log(f"  - 缺失: {result.missing_phone_count}", Fore.YELLOW)
        if result.invalid_phone_count > 0:
            self._log(f"  - 格式异常: {result.invalid_phone_count}", Fore.YELLOW)
        if total_bad == 0:
            self._log(f"  - 全部有效 ✓", Fore.GREEN)
        else:
            self._log(f"  - 总计异常: {total_bad}/{len(df)}", Fore.RED if total_bad > len(df) * 0.1 else Fore.YELLOW)

    def _check_wechat_column(self, df: pd.DataFrame, result: PrecheckResult):
        if 'wechat' not in df.columns:
            return

        wx_col = df['wechat']
        wx_norm = wx_col.apply(clean_wechat)
        empty_mask = wx_norm == ""
        result.missing_wechat_count = int(empty_mask.sum())

        invalid_mask = ~empty_mask & ~wx_norm.apply(
            lambda x: validate_wechat(x, self.config.wechat_regex)
        )
        result.invalid_wechat_count = int(invalid_mask.sum())
        result.invalid_wechat_rows = df.index[invalid_mask].tolist()

        self._log(f"💬 微信号检查:", Fore.CYAN)
        if result.missing_wechat_count > 0:
            self._log(f"  - 未填写: {result.missing_wechat_count}", Fore.LIGHTBLACK_EX)
        if result.invalid_wechat_count > 0:
            self._log(f"  - 格式异常: {result.invalid_wechat_count}", Fore.YELLOW)
            if result.invalid_wechat_rows:
                examples = []
                for idx in result.invalid_wechat_rows[:3]:
                    examples.append(f"行{idx+2}: {wx_col.iloc[idx]}")
                self._log(f"    例: {', '.join(examples)}", Fore.LIGHTBLACK_EX)
        if result.invalid_wechat_count == 0 and result.missing_wechat_count < len(df):
            self._log(f"  - 有效 ✓", Fore.GREEN)

    def _check_empty_rows(self, df: pd.DataFrame, result: PrecheckResult):
        key_cols = [c for c in ['phone', 'wechat', 'name'] if c in df.columns]
        if not key_cols:
            result.warnings.append("没有可用于识别的关键字段")
            return

        all_empty = df[key_cols].apply(
            lambda row: all(pd.isna(v) or str(v).strip() == "" for v in row), axis=1
        )
        result.empty_rows = int(all_empty.sum())
        if result.empty_rows > 0:
            self._log(f"⚠  完全空白行: {result.empty_rows}", Fore.YELLOW)
            result.warnings.append(f"{result.empty_rows} 行无任何有效联系方式")

    def _check_internal_duplicates(self, df: pd.DataFrame, result: PrecheckResult):
        dup_count = 0

        if '_phone_norm' in df.columns:
            non_empty = df[df['_phone_norm'] != ""]['_phone_norm']
            dup_count += int((non_empty.duplicated()).sum())

        if '_wechat_norm' in df.columns:
            non_empty = df[df['_wechat_norm'] != ""]['_wechat_norm']
            dup_count += int((non_empty.duplicated()).sum())

        result.duplicate_in_file = dup_count
        if dup_count > 0:
            self._log(f"🔁 文件内疑似重复线索: ~{dup_count} 项", Fore.YELLOW)

    def _compute_field_stats(self, df: pd.DataFrame, result: PrecheckResult):
        for col in ['channel', 'city', 'intention', 'consultant', 'store']:
            if col in df.columns:
                vc = df[col].value_counts(dropna=True).head(10)
                result.field_stats[col] = {
                    'unique': int(df[col].nunique(dropna=True)),
                    'top': vc.to_dict()
                }

    def print_summary(self, result: PrecheckResult):
        self._log("\n" + "=" * 60, Fore.CYAN)
        self._log("📋 预检报告汇总", Fore.CYAN + Style.BRIGHT)
        self._log("=" * 60, Fore.CYAN)
        self._log(f"  总行数:         {result.total_rows}")
        self._log(f"  缺失手机号:     {result.missing_phone_count}")
        self._log(f"  手机号异常:     {result.invalid_phone_count}")
        self._log(f"  微信号异常:     {result.invalid_wechat_count}")
        self._log(f"  空白行:         {result.empty_rows}")
        self._log(f"  文件内重复:     ~{result.duplicate_in_file}")

        if result.warnings:
            self._log(f"\n  ⚠ 警告信息:", Fore.YELLOW)
            for w in result.warnings:
                self._log(f"    - {w}", Fore.YELLOW)

        if result.field_stats:
            self._log(f"\n  📊 字段分布:", Fore.CYAN)
            for field, stats in result.field_stats.items():
                self._log(f"    [{field}] {stats['unique']}个值 → Top5:", Fore.LIGHTCYAN_EX)
                items = list(stats['top'].items())[:5]
                for k, v in items:
                    self._log(f"       {k}: {v}", Fore.LIGHTBLACK_EX)

        score = self._calc_health_score(result)
        color = Fore.GREEN if score >= 80 else (Fore.YELLOW if score >= 50 else Fore.RED)
        self._log(f"\n  🎯 数据质量分: {score}/100", color + Style.BRIGHT)
        self._log("=" * 60 + "\n", Fore.CYAN)

    def _calc_health_score(self, r: PrecheckResult) -> int:
        if r.total_rows == 0:
            return 0
        score = 100
        score -= (r.missing_phone_count / r.total_rows) * 40
        score -= (r.invalid_phone_count / r.total_rows) * 20
        score -= (r.invalid_wechat_count / max(r.total_rows - r.missing_wechat_count, 1)) * 15
        score -= (r.empty_rows / r.total_rows) * 20
        score -= min((r.duplicate_in_file / r.total_rows) * 20, 15)
        return max(0, int(score))
