import requests
import hashlib
import datetime
import os

# =========================
# 基本配置
# =========================

USERID = os.getenv("USERID", "1533760024")
MTOKEN = os.getenv("MTOKEN", "")

OUTPUT_M3U = "live.m3u"
OUTPUT_TXT = "live.txt"

M3U_HEADER = "#EXTM3U\n"

# =========================
# 工具函数
# =========================

def writefile(path, content, mode='a'):
    with open(path, mode, encoding="utf-8") as f:
        f.write(content)


def format_date_ymd():
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")


# =========================
# 简单分类
# =========================

def classify_channel(name):
    if "CCTV" in name:
        return "央视频道"
    if "卫视" in name:
        return "卫视频道"
    if "电影" in name or "影院" in name:
        return "电影频道"
    return "其他频道"


# =========================
# 获取频道列表
# =========================

def fetch_channels():
    url = "https://display.miguvideo.com/live/videox/staticcache/basic/allChannel.json"

    try:
        r = requests.get(url, timeout=10)
        data = r.json()
    except:
        print("频道列表获取失败，使用备用测试数据")
        return [
            {"name": "CCTV1综合", "contId": "624878396"},
            {"name": "CCTV5体育", "contId": "641886683"},
            {"name": "CCTV6电影", "contId": "624878396"},
        ]

    channels = []
    for c in data.get("data", []):
        channels.append({
            "name": c.get("name"),
            "contId": c.get("contId")
        })

    return channels


# =========================
# 获取播放地址
# =========================

def fetch_play_url(contId):
    url = f"https://display.miguvideo.com/live/videox/player/playurl?contId={contId}"

    try:
        r = requests.get(url, timeout=10)
        data = r.json()
    except:
        return None

    try:
        return data["body"]["urlInfo"]["url"]
    except:
        return None


# =========================
# 主流程
# =========================

def main():

    print("开始获取频道...")

    channels = fetch_channels()

    writefile(OUTPUT_M3U, M3U_HEADER, 'w')
    writefile(OUTPUT_TXT, "", 'w')

    total = 0

    for ch in channels:
        name = ch["name"]
        contId = ch["contId"]

        print("获取:", name)

        play_url = fetch_play_url(contId)

        if not play_url:
            print("失败:", name)
            continue

        group = classify_channel(name)

        m3u_line = (
            f'#EXTINF:-1 tvg-name="{name}" '
            f'group-title="{group}",{name}\n'
            f'{play_url}\n'
        )

        txt_line = f"{name},{play_url}\n"

        writefile(OUTPUT_M3U, m3u_line)
        writefile(OUTPUT_TXT, txt_line)

        total += 1

    print("\n完成")
    print("频道数:", total)
    print("输出:", OUTPUT_M3U, OUTPUT_TXT)


# =========================
# 启动
# =========================

if __name__ == "__main__":
    main()
