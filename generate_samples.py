import os
import pandas as pd
import random
from datetime import datetime, timedelta

random.seed(42)

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
GIVEN1 = list("伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英文华")
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "武汉", "西安", "重庆",
           "苏州", "天津", "长沙", "郑州", "青岛", "宁波", "无锡", "厦门"]
INTENTIONS = ["双眼皮", "隆鼻", "玻尿酸", "水光针", "热玛吉", "超声刀", "隆胸", "吸脂",
            "光子嫩肤", "皮秒", "线雕", "瘦脸针", "除皱针", "填充", "热拉提", "皮秒祛斑"]
CHANNELS = ["抖音", "小红书", "美团", "新氧", "百度", "朋友圈广告", "大众点评"]
CONSULTANTS = ["张老师", "李老师", "王医生", "刘主任", "陈老师", "杨老师"]
STORES = ["总院", "浦东分院", "徐汇分院"]


def random_name():
    return random.choice(SURNAMES) + random.choice(GIVEN1) + (random.choice(GIVEN1) if random.random() > 0.5 else "")


def random_phone():
    prefixes = ["138", "139", "150", "151", "152", "158", "159", "186", "187", "188", "189", "135", "136", "137"]
    return random.choice(prefixes) + "".join([str(random.randint(0, 9)) for _ in range(8)])


def random_wechat():
    prefix = random.choice(["wx", "wxid_", "lucky", "beauty", "angel", "mm", "qq"])
    return prefix + str(random.randint(10000, 999999))


def random_birthday():
    start = datetime(1980, 1, 1)
    end = datetime(2005, 12, 31)
    delta = end - start
    d = start + timedelta(days=random.randint(0, delta.days))
    return d.strftime("%Y-%m-%d")


def random_date_2024():
    start = datetime(2024, 1, 1)
    end = datetime(2024, 12, 31)
    delta = end - start
    d = start + timedelta(days=random.randint(0, delta.days))
    return d.strftime("%Y-%m-%d")


def make_campaign(n=300):
    rows = []
    for i in range(n):
        rows.append({
            "客户姓名": random_name(),
            "手机号": random_phone(),
            "微信号": random_wechat() if random.random() > 0.3 else "",
            "性别": random.choice(["女", "男", ""]),
            "年龄": random.randint(20, 55),
            "生日": random_birthday() if random.random() > 0.5 else "",
            "所在城市": random.choice(CITIES),
            "项目意向": random.choice(INTENTIONS),
            "投放渠道": random.choice(CHANNELS),
            "咨询师": random.choice(CONSULTANTS),
            "录入日期": random_date_2024(),
        })
    df = pd.DataFrame(rows)

    for i in range(20):
        dup = df.iloc[i].copy()
        dup["投放渠道"] = random.choice(CHANNELS)
        dup["咨询师"] = random.choice(CONSULTANTS)
        df = pd.concat([df, pd.DataFrame([dup])], ignore_index=True)

    return df.reset_index(drop=True)


def make_history(n=500, campaign_df=None):
    rows = []
    for i in range(n):
        rows.append({
            "姓名": random_name(),
            "手机号码": random_phone(),
            "微信": random_wechat() if random.random() > 0.4 else "",
            "出生年月日": random_birthday(),
            "城市": random.choice(CITIES),
            "咨询项目": random.choice(INTENTIONS),
            "消费金额": random.randint(3000, 80000),
            "成交日期": random_date_2024(),
            "负责咨询师": random.choice(CONSULTANTS),
            "所属门店": random.choice(STORES),
            "来源渠道": random.choice(CHANNELS),
        })
    df = pd.DataFrame(rows)
    if campaign_df is not None and len(campaign_df) >= 15:
        for i in range(15):
            old = df.iloc[i].copy()
            old["手机号码"] = campaign_df.iloc[i]["手机号"]
            old["姓名"] = campaign_df.iloc[i]["客户姓名"]
            if pd.notna(campaign_df.iloc[i]["生日"]) and campaign_df.iloc[i]["生日"]:
                old["出生年月日"] = campaign_df.iloc[i]["生日"]
            df = pd.concat([df, pd.DataFrame([old])], ignore_index=True)
    return df.reset_index(drop=True)


def make_following(n=200, campaign_df=None):
    rows = []
    for i in range(n):
        rows.append({
            "姓名": random_name(),
            "手机号": random_phone(),
            "微信号": random_wechat(),
            "城市": random.choice(CITIES),
            "意向项目": random.choice(INTENTIONS),
            "跟进人": random.choice(CONSULTANTS),
            "分院": random.choice(STORES),
            "跟进状态": random.choice(["在跟", "待回访", "已约到店", "犹豫中"]),
        })
    df = pd.DataFrame(rows)
    if campaign_df is not None and len(campaign_df) >= 10:
        for i in range(40, 50):
            f_row = df.iloc[i].copy()
            f_row["手机号"] = campaign_df.iloc[i]["手机号"]
            f_row["姓名"] = campaign_df.iloc[i]["客户姓名"]
            df = pd.concat([df, pd.DataFrame([f_row])], ignore_index=True)
    return df.reset_index(drop=True)


if __name__ == "__main__":
    os.makedirs("sample_data", exist_ok=True)

    campaign = make_campaign(300)
    campaign_path = os.path.join("sample_data", "6月抖音投放.xlsx")
    campaign.to_excel(campaign_path, index=False)
    print(f"生成: {campaign_path} ({len(campaign)} 行)")

    history = make_history(500, campaign)
    history_path = os.path.join("sample_data", "历史成交客户.xlsx")
    history.to_excel(history_path, index=False)
    print(f"生成: {history_path} ({len(history)} 行)")

    following = make_following(200, campaign)
    following_path = os.path.join("sample_data", "门店在跟名单.xlsx")
    following.to_excel(following_path, index=False)
    print(f"生成: {following_path} ({len(following)} 行)")

    campaign2 = make_campaign(280)
    campaign2_path = os.path.join("sample_data", "5月小红书投放.xlsx")
    campaign2.to_excel(campaign2_path, index=False)
    print(f"生成: {campaign2_path} ({len(campaign2)} 行)")

    print("\n✅ 示例数据已生成到 sample_data/ 目录")
