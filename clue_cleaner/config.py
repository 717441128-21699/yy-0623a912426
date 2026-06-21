import os
import yaml
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

_DEFAULT_SCORE_THRESHOLDS = {
    'definite_dup': 90,
    'probable_dup': 70,
    'possible_dup': 50
}

_DEFAULT_OUTPUT = {
    'encoding': 'utf-8-sig',
    'date_format': '%Y%m%d_%H%M%S'
}


@dataclass
class AppConfig:
    field_aliases: Dict[str, list] = field(default_factory=dict)
    dedup_rules: Dict[str, Any] = field(default_factory=dict)
    channel_priority: Dict[str, int] = field(default_factory=dict)
    phone_regex: str = r'^1[3-9]\d{9}$'
    wechat_regex: str = r'^[a-zA-Z][a-zA-Z0-9_-]{5,19}$'
    score_thresholds: Dict[str, int] = field(default_factory=lambda: dict(_DEFAULT_SCORE_THRESHOLDS))
    output: Dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_OUTPUT))
    config_path: str = ""

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> 'AppConfig':
        if config_path is None:
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')

        config_path = os.path.abspath(config_path)

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            raw = yaml.safe_load(f) or {}

        return cls(
            field_aliases=raw.get('field_aliases', {}),
            dedup_rules=raw.get('dedup_rules', {}),
            channel_priority=raw.get('channel_priority', {}),
            phone_regex=raw.get('phone_regex', cls.__dataclass_fields__['phone_regex'].default),
            wechat_regex=raw.get('wechat_regex', cls.__dataclass_fields__['wechat_regex'].default),
            score_thresholds=raw.get('score_thresholds', dict(_DEFAULT_SCORE_THRESHOLDS)),
            output=raw.get('output', dict(_DEFAULT_OUTPUT)),
            config_path=config_path
        )

    def save(self, config_path: Optional[str] = None) -> str:
        save_path = config_path or self.config_path
        data = {
            'field_aliases': self.field_aliases,
            'dedup_rules': self.dedup_rules,
            'channel_priority': self.channel_priority,
            'phone_regex': self.phone_regex,
            'wechat_regex': self.wechat_regex,
            'score_thresholds': self.score_thresholds,
            'output': self.output
        }
        with open(save_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return save_path

    def get_alias_reverse_map(self) -> Dict[str, str]:
        reverse = {}
        for canonical, aliases in self.field_aliases.items():
            reverse[canonical.lower()] = canonical
            for alias in aliases:
                reverse[str(alias).lower().strip()] = canonical
        return reverse

    def get_channel_priority(self, channel_name: str) -> int:
        if not channel_name:
            return 999
        name = str(channel_name).strip()
        for key, priority in self.channel_priority.items():
            if key in name or name in key:
                return priority
        return 999
