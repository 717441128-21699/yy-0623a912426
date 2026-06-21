import os
import re
import glob
import pandas as pd
from typing import List, Dict, Tuple, Optional, Any
from colorama import Fore, Style
from .config import AppConfig
from .models import (
    FieldMapping, SourceType, clean_phone, clean_wechat,
    clean_name, clean_city, clean_intention
)


class DataLoader:
    def __init__(self, config: AppConfig, verbose: bool = True):
        self.config = config
        self.verbose = verbose
        self.alias_map = config.get_alias_reverse_map()

    def _log(self, msg: str, color: str = Fore.WHITE):
        if self.verbose:
            print(f"{color}{msg}{Style.RESET_ALL}")

    def _read_file(self, filepath: str) -> pd.DataFrame:
        ext = os.path.splitext(filepath)[1].lower()
        if ext in ['.xlsx', '.xls']:
            return pd.read_excel(filepath, dtype=str)
        elif ext == '.csv':
            for enc in ['utf-8-sig', 'utf-8', 'gbk', 'gb18030']:
                try:
                    return pd.read_csv(filepath, dtype=str, encoding=enc)
                except UnicodeDecodeError:
                    continue
            raise ValueError(f"无法识别的CSV编码: {filepath}")
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

    def _match_columns(self, columns: List[str]) -> Tuple[List[FieldMapping], Dict[str, str]]:
        mappings = []
        col_rename = {}

        for col in columns:
            col_lower = str(col).lower().strip()
            matched = self.alias_map.get(col_lower)
            if matched:
                mappings.append(FieldMapping(
                    source_column=col,
                    canonical_field=matched,
                    match_type="exact"
                ))
                col_rename[col] = matched
            else:
                for alias_lower, canonical in self.alias_map.items():
                    if alias_lower and (alias_lower in col_lower or col_lower in alias_lower):
                        if len(alias_lower) >= 2:
                            mappings.append(FieldMapping(
                                source_column=col,
                                canonical_field=canonical,
                                match_type="fuzzy"
                            ))
                            col_rename[col] = canonical
                            break

        return mappings, col_rename

    def load_single(self, filepath: str, source_type: SourceType,
                    source_label: Optional[str] = None) -> Tuple[pd.DataFrame, List[FieldMapping]]:
        self._log(f"📂 加载文件: {filepath}", Fore.CYAN)

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")

        df = self._read_file(filepath)
        self._log(f"   - 读取 {len(df)} 行, {len(df.columns)} 列")

        mappings, col_rename = self._match_columns(df.columns.tolist())
        df = df.rename(columns=col_rename)

        for m in mappings:
            tag = "✓" if m.match_type == "exact" else "~"
            self._log(f"   {tag} {m.source_column} → {m.canonical_field}",
                      Fore.GREEN if m.match_type == "exact" else Fore.YELLOW)

        unmapped = [c for c in df.columns if c not in set(m.canonical_field for m in mappings)]
        if unmapped:
            self._log(f"   ⚠ 未识别字段: {', '.join(unmapped[:10])}{'...' if len(unmapped) > 10 else ''}",
                      Fore.YELLOW)

        df['_source_file'] = os.path.basename(filepath)
        df['_source_type'] = source_type.value
        df['_source_label'] = source_label or source_type.value
        df['_raw_index'] = range(len(df))

        return df, mappings

    def load_multiple(self, file_patterns: List[str], source_type: SourceType,
                      source_label: Optional[str] = None) -> Tuple[pd.DataFrame, Dict[str, List[FieldMapping]]]:
        all_dfs = []
        all_mappings = {}

        files = []
        for pattern in file_patterns:
            matched = glob.glob(pattern)
            if matched:
                files.extend(matched)
            elif os.path.exists(pattern):
                files.append(pattern)

        files = list(dict.fromkeys(files))

        if not files:
            self._log(f"⚠ 未找到匹配的文件: {file_patterns}", Fore.RED)
            return pd.DataFrame(), all_mappings

        self._log(f"🔍 找到 {len(files)} 个文件待加载", Fore.CYAN)

        for f in files:
            try:
                df, mappings = self.load_single(f, source_type, source_label)
                all_dfs.append(df)
                all_mappings[f] = mappings
            except Exception as e:
                self._log(f"✗ 加载失败 {f}: {e}", Fore.RED)

        if not all_dfs:
            return pd.DataFrame(), all_mappings

        merged = pd.concat(all_dfs, ignore_index=True, sort=False)
        self._log(f"✅ 合并完成: 共 {len(merged)} 行", Fore.GREEN)

        return merged, all_mappings

    def normalize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.copy()

        if 'phone' in df.columns:
            df['_phone_norm'] = df['phone'].apply(clean_phone)
        if 'wechat' in df.columns:
            df['_wechat_norm'] = df['wechat'].apply(clean_wechat)
        if 'name' in df.columns:
            df['_name_norm'] = df['name'].apply(clean_name)
        if 'birthday' in df.columns:
            df['_birthday_norm'] = df['birthday'].apply(
                lambda x: re.sub(r'[^\d]', '', str(x))[:8] if not pd.isna(x) else ""
            )
        if 'city' in df.columns:
            df['_city_norm'] = df['city'].apply(clean_city)
        if 'intention' in df.columns:
            df['_intention_norm'] = df['intention'].apply(clean_intention)
        if 'channel' not in df.columns:
            df['channel'] = df.get('_source_label', '未知渠道')

        return df
