import re
import pandas as pd
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class SourceType(Enum):
    CAMPAIGN = "campaign"
    HISTORY = "history"
    FOLLOWING = "following"


class DedupLevel(Enum):
    DEFINITE = "确定重复"
    PROBABLE = "大概率重复"
    POSSIBLE = "疑似重复"
    UNIQUE = "独立线索"
    OLD_CUSTOMER = "老客复咨"
    FOLLOWING = "在跟名单"


@dataclass
class FieldMapping:
    source_column: str
    canonical_field: str
    match_type: str = "exact"

    def __repr__(self):
        return f"{self.source_column} → {self.canonical_field}"


@dataclass
class PrecheckResult:
    total_rows: int = 0
    field_mappings: List[FieldMapping] = field(default_factory=list)
    missing_phone_count: int = 0
    missing_phone_rows: List[int] = field(default_factory=list)
    invalid_phone_count: int = 0
    invalid_phone_rows: List[int] = field(default_factory=list)
    missing_wechat_count: int = 0
    invalid_wechat_count: int = 0
    invalid_wechat_rows: List[int] = field(default_factory=list)
    empty_rows: int = 0
    duplicate_in_file: int = 0
    warnings: List[str] = field(default_factory=list)
    field_stats: Dict[str, Dict] = field(default_factory=dict)


@dataclass
class DedupMatch:
    rule_name: str
    matched_fields: Dict[str, str]
    score: float
    target_index: Any
    source_index: Any
    source_type: SourceType
    match_detail: str = ""


@dataclass
class DedupRecord:
    row_index: Any
    original_row: Dict[str, Any]
    score: float = 0.0
    level: DedupLevel = DedupLevel.UNIQUE
    matched_rules: List[DedupMatch] = field(default_factory=list)
    matched_with: List[Any] = field(default_factory=list)
    final_channel: str = ""
    original_channel: str = ""
    hit_channel: str = ""
    linked_channels: List[str] = field(default_factory=list)
    is_old_customer: bool = False
    is_in_following: bool = False
    keep_row: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.original_row)
        d['_row_index'] = self.row_index
        d['_score'] = round(self.score, 1)
        d['_level'] = self.level.value
        d['_matched_rules'] = '; '.join([m.rule_name for m in self.matched_rules])
        d['_matched_with'] = '; '.join([str(x) for x in self.matched_with])
        d['_original_channel'] = self.original_channel
        d['_hit_channel'] = self.hit_channel
        d['_final_channel'] = self.final_channel
        d['_linked_channels'] = '; '.join(sorted(set(self.linked_channels)))
        d['_is_old_customer'] = '是' if self.is_old_customer else '否'
        d['_is_in_following'] = '是' if self.is_in_following else '否'
        d['_keep'] = '保留' if self.keep_row else '剔除'
        return d


@dataclass
class BatchCompareResult:
    batch_a_rows: int = 0
    batch_b_rows: int = 0
    new_in_b: int = 0
    removed_in_b: int = 0
    common_rows: int = 0
    channel_diff: Dict[str, Dict] = field(default_factory=dict)
    score_diff: List[Dict] = field(default_factory=list)


def clean_phone(phone: Any) -> str:
    if pd.isna(phone) or phone is None:
        return ""
    s = re.sub(r'[^\d]', '', str(phone))
    if len(s) == 11 and s.startswith('86'):
        s = s[2:]
    if len(s) > 11 and s.startswith('86'):
        s = s[2:13]
    return s[:11]


def clean_wechat(wx: Any) -> str:
    if pd.isna(wx) or wx is None:
        return ""
    s = str(wx).strip()
    s = re.sub(r'[\s\u3000]', '', s)
    return s


def clean_name(name: Any) -> str:
    if pd.isna(name) or name is None:
        return ""
    s = str(name).strip()
    s = re.sub(r'[\s·\.]', '', s)
    return s


def clean_city(city: Any) -> str:
    if pd.isna(city) or city is None:
        return ""
    s = str(city).strip()
    s = re.sub(r'[市县区镇省]', '', s)
    return s


def clean_intention(intent: Any) -> str:
    if pd.isna(intent) or intent is None:
        return ""
    s = str(intent).strip()
    s = re.sub(r'[\s、,，/\\|]', '', s)
    return s.lower()


def validate_phone(phone: str, regex_pattern: str) -> bool:
    if not phone:
        return False
    return bool(re.match(regex_pattern, phone))


def validate_wechat(wx: str, regex_pattern: str) -> bool:
    if not wx:
        return True
    return bool(re.match(regex_pattern, wx))
