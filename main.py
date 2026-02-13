import requests
import json
import time
import random
import hashlib
import re
import unicodedata
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import os

# -------------------------- 核心配置 --------------------------
LOCAL_EPG_CACHE = "epg.xml"
thread_mum = 10

headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Origin": "https://m.miguvideo.com",
    "Pragma": "no-cache",
    "Referer": "https://m.miguvideo.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "appCode": "miguvideo_default_h5",
    "appId": "miguvideo",
    "channel": "H5",
    "terminalId": "h5"
}

lives = ['热门', '央视', '卫视', '地方', '体育', '影视', '综艺', '少儿', '新闻', '教育', '熊猫', '纪实']
LIVE = {
    '热门': 'e7716fea6aa1483c80cfc10b7795fcb8',
    '体育': '7538163cdac044398cb292ecf75db4e0',
    '央视': '1ff892f2b5ab4a79be6e25b69d2f5d05',
    '卫视': '0847b3f6c08a4ca28f85ba5701268424',
    '地方': '855e9adc91b04ea18ef3f2dbd43f495b',
    '影视': '10b0d04cb23d4ac5945c4bc77c7ac44e',
    '新闻': 'c584f67ad63f4bc983c31de3a9be977c',
    '教育': 'af72267483d94275995a4498b2799ecd',
    '熊猫': 'e76e56e88fff4c11b0168f55e826445d',
    '综艺': '192a12edfef04b5eb616b878f031f32f',
    '少儿': 'fc2f5b8fd7db43ff88c4243e731ecede',
    '纪实': 'e1165138bdaa44b9a3138d74af6c6673'
}

m3u_path = 'migu.m3u'
txt_path = 'migu.txt'
M3U_HEADER = '#EXTM3U\n'

channels_dict = {}
processed_pids = set()
FLAG = 0

appVersion = "60003000"  # TV版版本号


def extract_cctv_number(channel_name):
    match = re.search(r'CCTV[-\s]?(\d+)', channel_name)
    if match:
        try:
            return int(match.group(1))
        except:
            return 999
    if 'CCTV' in channel_name:
        if 'CGTN' in channel_name:
            if '法语' in channel_name:
                return 1001
            elif '西班牙语' in channel_name:
                return 1002
            elif '俄语' in channel_name:
                return 1003
            elif '阿拉伯语' in channel_name:
                return 1004
            elif '外语纪录' in channel_name:
                return 1005
            else:
                return 1000
        elif '美洲' in channel_name:
            return 1006
        elif '欧洲' in channel_name:
            return 1007
    return 9999


def extract_panda_number(channel_name):
    zero_match = re.search(r'熊猫0(\d+)', channel_name)
    if zero_match:
        try:
            num = int(zero_match.group(1))
            return (0, num)
        except:
            return (999, 999)
    normal_match = re.search(r'熊猫(\d+)', channel_name)
    if normal_match:
        try:
            num = int(normal_match.group(1))
            return (1, num)
        except:
            return (999, 999)
    return (9999, 9999)


def extract_satellite_first_char(channel_name):
    if not channel_name:
        return 'z'
    first_char = channel_name[0]
    normalized_char = unicodedata.normalize('NFKC', first_char)
    return normalized_char


def get_sort_key(channel_name):
    if 'CCTV' in channel_name:
        cctv_num = extract_cctv_number(channel_name)
        return (0, cctv_num, channel_name)
    if '熊猫' in channel_name:
        panda_num = extract_panda_number(channel_name)
        return (1, panda_num, channel_name)
    if is_satellite_channel(channel_name):
        first_char = extract_satellite_first_char(channel_name)
        return (2, first_char, channel_name)
    return (3, channel_name)


def is_cctv_channel(channel_name):
    return 'CCTV' in channel_name or 'CGTN' in channel_name


def is_satellite_channel(channel_name):
    return '卫视' in channel_name and 'CCTV' not in channel_name


def smart_classify_5_categories(channel_name):
    if channel_name in channels_dict:
        return None
    if '熊猫' in channel_name:
        return '🐼熊猫频道'
    if is_cctv_channel(channel_name):
        return '📺央视频道'
    if is_satellite_channel(channel_name):
        return '📡卫视频道'
    lower_name = channel_name.lower()
    entertainment_keywords = ['电影', '影视', '影院', '影迷', '少儿', '卡通', '动漫', '动画', '综艺', '戏曲', '音乐', '秦腔', '嘉佳', '优漫', '新动漫', '经典动画']
    for kw in entertainment_keywords:
        if kw in channel_name:
            return '🎬影音娱乐'
    return '📰生活资讯'


def format_date_ymd():
    current_date = datetime.now()
    return f"{current_date.year}{current_date.month:02d}{current_date.day:02d}"


def writefile(path, content, mode='w'):
    with open(path, mode, encoding='utf-8') as f:
        f.write(content)


def md5(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()


# ====================== 720P 播放地址获取（已修复）======================
def get_real_720p_playurl(pid):
    timestamp = str(int(time.time() * 1000))
    rand = random.randint(0, 999999)
    salt = f"{rand:06d}25"
    suffix = "2cac4f2c6c3346a5b34e085725ef7e33migu" + salt[:4]
    app_t = timestamp + pid + appVersion
    sign = md5(md5(app_t) + suffix)

    # 720P
    rateType = "4"

    url = (
        f"https://play.miguvideo.com/play/v1/playurl"
        f"?contId={pid}&rateType={rateType}"
        f"&platform=androidtv&osVersion=12&vendor=xiaomi&playRealm=0"
        f"&timestamp={timestamp}&salt={salt}&sign={sign}"
    )

    tv_headers = {
        "User-Agent": "AndroidTV7.0;1080p",
        "appId": "miguvideoandroidtv",
        "terminalId": "androidtv",
        "channel": "androidtv",
        "appVersion": appVersion,
        "Accept": "application/json, text/plain, */*",
    }

    try:
        resp = requests.get(url, headers=tv_headers, timeout=8).json()
        playurl = resp["body"]["urlInfo"]["url"]
    except:
        return None

    # 追真实m3u8
    for _ in range(6):
        try:
            r = requests.get(playurl, headers=tv_headers, allow_redirects=False, timeout=3)
            if "Location" in r.headers:
                playurl = r.headers["Location"]
            else:
                break
        except:
            break
    return playurl


def append_All_Live(live, flag, data):
    try:
        if data["pID"] in processed_pids:
            return
        processed_pids.add(data["pID"])

        # 直接获取720P地址
        real_url = get_real_720p_playurl(data["pID"])
        if not real_url:
            print(f'频道 [{data["name"]}] 获取720P地址失败')
            return

        ch_name = data["name"]
        if "CCTV" in ch_name:
            ch_name = ch_name.replace("CCTV", "CCTV-")
        if "熊猫" in ch_name:
            ch_name = ch_name.replace("高清", "")

        category = smart_classify_5_categories(ch_name)
        if category is None:
            return

        sort_key = get_sort_key(ch_name)
        m3u_item = f'#EXTINF:-1 group-title="{category}",{ch_name}\n{real_url}\n'
        txt_item = f"{ch_name},{real_url}\n"
        channels_dict[ch_name] = [m3u_item, txt_item, category, sort_key]
        print(f'✅ 720P [{ch_name}]【{category}】成功')

    except Exception as e:
        print(f'❌ [{data["name"]}] 失败：{e}')


def update(live, url):
    global FLAG
    pool = ThreadPoolExecutor(thread_mum)
    try:
        response = requests.get(url, headers=headers, timeout=10).json()
        dataList = response["body"]["dataList"]
    except:
        dataList = []

    for flag, data in enumerate(dataList):
        pool.submit(append_All_Live, live, FLAG + flag, data)
    pool.shutdown()
    FLAG += len(dataList)


def main():
    writefile(m3u_path, M3U_HEADER, 'w')
    writefile(txt_path, "", 'w')

    for live in lives:
        print(f"\n▸▸▸ 开始抓取分类: [{live}]")
        url = f'https://program-sc.miguvideo.com/live/v2/tv-data/{LIVE[live]}'
        update(live, url)

    category_channels = defaultdict(list)
    for ch_name, (m3u_item, txt_item, category, sort_key) in channels_dict.items():
        category_channels[category].append((sort_key, ch_name, m3u_item, txt_item))

    for cat in category_channels:
        category_channels[cat].sort(key=lambda x: x[0])

    category_order = [
        '📺央视频道',
        '📡卫视频道',
        '🐼熊猫频道',
        '🎬影音娱乐',
        '📰生活资讯'
    ]

    for cat in category_order:
        if cat in category_channels:
            for sk, name, m3u_it, txt_it in category_channels[cat]:
                writefile(m3u_path, m3u_it, 'a')

    for cat in category_order:
        if cat in category_channels and category_channels[cat]:
            writefile(txt_path, f"{cat},#genre#\n", 'a')
            for sk, name, m3u_it, txt_it in category_channels[cat]:
                writefile(txt_path, txt_it, 'a')

    total = len(channels_dict)
    print(f"\n🎉 全部完成！总计频道：{total}")
    print(f"📁 已生成：migu.m3u / migu.txt")
    print(f"🖥 清晰度：默认 720P 超清")


if __name__ == "__main__":
    main()
