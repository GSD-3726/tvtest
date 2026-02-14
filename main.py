import requests
import json
import time
import random
import hashlib
import re
import unicodedata  # 用于中文字符归一化，保证卫视频道首字母排序准确
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import os

# -------------------------- 核心配置 --------------------------
LOCAL_EPG_CACHE = "epg.xml"

thread_mum = 10
headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Origin": "https://m.miguvideo.com",
    "Pragma": "no-cache",
    "Referer": "https://m.miguvideo.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Support-Pendant": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "appCode": "miguvideo_default_h5",
    "appId": "miguvideo",
    "channel": "H5",
    "sec-ch-ua": "\"Chromium\";v=\"136\", \"Microsoft Edge\";v=\"136\", \"Not.A/Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "terminalId": "h5"
}

lives = ['热门', '央视', '卫视', '地方', '体育', '影视', '综艺', '少儿', '新闻', '教育', '熊猫', '纪实']
LIVE = {'热门': 'e7716fea6aa1483c80cfc10b7795fcb8', '体育': '7538163cdac044398cb292ecf75db4e0',
        '央视': '1ff892f2b5ab4a79be6e25b69d2f5d05', '卫视': '0847b3f6c08a4ca28f85ba5701268424',
        '地方': '855e9adc91b04ea18ef3f2dbd43f495b', '影视': '10b0d04cb23d4ac5945c4bc77c7ac44e',
        '新闻': 'c584f67ad63f4bc983c31de3a9be977c', '教育': 'af72267483d94275995a4498b2799ecd',
        '熊猫': 'e76e56e88fff4c11b0168f55e826445d', '综艺': '192a12edfef04b5eb616b878f031f32f',
        '少儿': 'fc2f5b8fd7db43ff88c4243e731ecede', '纪实': 'e1165138bdaa44b9a3138d74af6c6673'}

# -------------------------- 配置 --------------------------
m3u_path = 'migu.m3u'
txt_path = 'migu.txt'
M3U_HEADER = f'#EXTM3U\n'

channels_dict = {}
processed_pids = set()  # 用于跟踪已处理的PID
FLAG = 0

appVersion = "2600034600"
appVersionID = appVersion + "-99000-201600010010028"

# 用户ID和Token，确保此处信息是有效的
USERID = "1533760024"
MTOKEN = "nlps702651C26F6AE5D969C3"


def md5(text):
    md5_obj = hashlib.md5()
    md5_obj.update(text.encode('utf-8'))
    return md5_obj.hexdigest()


def getSaltAndSign(pid):
    timestamp = str(int(time.time() * 1000))
    random_num = random.randint(0, 999999)
    salt = f"{random_num:06d}25"
    suffix = "2cac4f2c6c3346a5b34e085725ef7e33migu" + salt[:4]
    app_t = timestamp + pid + appVersion[:8]
    sign = md5(md5(app_t) + suffix)
    return {
        "salt": salt,
        "sign": sign,
        "timestamp": timestamp
    }


def get_content(pid):
    result = getSaltAndSign(pid)
    rateType = "2" if pid == "608831231" else "3"
    URL = f"https://play.miguvideo.com/playurl/v1/play/playurl?sign={result['sign']}&rateType={rateType}&contId={pid}&timestamp={result['timestamp']}&salt={result['salt']}"
    response = requests.get(URL, headers=headers)
    if response.status_code == 200:
        return response.json()
    return None


def getddCalcu720p(url, pID):
    puData = url.split("&puData=")[1]
    keys = "cdabyzwxkl"
    ddCalcu = []
    for i in range(0, int(len(puData) / 2)):
        ddCalcu.append(puData[int(len(puData)) - i - 1])
        ddCalcu.append(puData[i])
        if i == 1:
            ddCalcu.append("v")
        if i == 2:
            ddCalcu.append(keys[int(format_date_ymd()[2])])
        if i == 3:
            ddCalcu.append(keys[int(pID[6])])
        if i == 4:
            ddCalcu.append("a")
    return f'{url}&ddCalcu={"".join(ddCalcu)}&sv=10004&ct=android'


def append_All_Live(live, flag, data):
    try:
        if data["pID"] in processed_pids:
            return
        processed_pids.add(data["pID"])

        respData = get_content(data["pID"])
        if respData:
            playurl = getddCalcu720p(respData["body"]["urlInfo"]["url"], data["pID"])

            if playurl != "":
                # 处理视频链接
                ch_name = data["name"]
                if "CCTV" in ch_name:
                    ch_name = ch_name.replace("CCTV", "CCTV-")
                if "熊猫" in ch_name:
                    ch_name = ch_name.replace("高清", "") 

                # 智能分类（使用5分类方案）
                category = smart_classify_5_categories(ch_name)
                if category is None:
                    return  # 频道已存在，跳过

                # 获取排序键（使用修改后的排序规则）
                sort_key = get_sort_key(ch_name)

                # 构建M3U和TXT条目
                m3u_item = f'#EXTINF:-1 group-title="{category}",{ch_name}\n{playurl}\n'
                txt_item = f"{ch_name},{playurl}\n"

                # 存储到字典
                channels_dict[ch_name] = [m3u_item, txt_item, category, sort_key]
                print(f'频道 [{ch_name}]【{category}】更新成功！')
        else:
            print(f'频道 [{data["name"]}] 更新失败！')
    except Exception as e:
        print(f'频道 [{data["name"]}] 更新失败！')
        print(f"ERROR:{e}")


def update(live, url):
    global FLAG
    pool = ThreadPoolExecutor(thread_mum)
    response = requests.get(url, headers=headers).json()
    dataList = response["body"]["dataList"]
    for flag, data in enumerate(dataList):
        pool.submit(append_All_Live, live, FLAG + flag, data)
    pool.shutdown()
    FLAG += len(dataList)


def main():
    # 1. 初始化文件
    writefile(m3u_path, M3U_HEADER, 'w')
    writefile(txt_path, "", 'w')

    # 2. 遍历爬取
    for live in lives:
        print(f"\n分类 ----- [{live}] ----- 开始更新. . .")
        url = f'https://program-sc.miguvideo.com/live/v2/tv-data/{LIVE[live]}'
        update(live, url)

    # 3. 按分类组织频道数据
    category_channels = defaultdict(list)

    for ch_name, (m3u_item, txt_item, category, sort_key) in channels_dict.items():
        category_channels[category].append((sort_key, ch_name, m3u_item, txt_item))

    # 4. 对每个分类下的频道进行排序（从小到大）
    for category in category_channels:
        category_channels[category].sort(key=lambda x: x[0])

    # 5. 按分类顺序写入m3u文件
    category_order = [
        '📺央视频道',
        '📡卫视频道',
        '🐼熊猫频道',
        '🎬影音娱乐',
        '📰生活资讯'
    ]

    for category in category_order:
        if category in category_channels:
            for sort_key, ch_name, m3u_item, txt_item in category_channels[category]:
                writefile(m3u_path, m3u_item, 'a')

    # 6. 按分类写入txt文件
    for category in category_order:
        if category in category_channels and category_channels[category]:
            writefile(txt_path, f"{category},#genre#\n", 'a')
            for sort_key, ch_name, m3u_item, txt_item in category_channels[category]:
                writefile(txt_path, txt_item, 'a')

    # 7. 输出统计信息
    total_channels = len(channels_dict)

    category_stats = {}
    for category in category_order:
        if category in category_channels:
            category_stats[category] = len(category_channels[category])
        else:
            category_stats[category] = 0

    print(f"\n✅ 双格式文件生成完成！")
    print(f"📁 M3U格式：{m3u_path}")
    print(f"📁 TXT格式：{txt_path}")
    print(f"📊 总计频道数：{total_channels}")

    print("\n📋 5分类统计：")
    for category in category_order:
        count = category_stats[category]
        percentage = (count / total_channels * 100) if total_channels > 0 else 0
        print(f"  {category}: {count} 个 ({percentage:.1f}%)")


if __name__ == "__main__":
    main()
