import requests
import time
import statistics
from urllib.parse import urljoin
import re

# ===================== 配置参数 =====================
# 远程播放列表地址（改用raw地址，避免GitHub blob页面的HTML干扰）
REMOTE_RESULT_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/GSD-3726/IPTV/master/output/result.txt"
# 测速配置
TEST_SHARD_COUNT = 5  # 每个链接测试的分片数量（批量检测建议减少，提升速度）
TIMEOUT = 5  # 每个分片下载超时时间（秒）
# 卡顿判定阈值（可根据需求调整）
FAIL_RATE_THRESHOLD = 0.1  # 失败率≤10% 视为不卡顿
AVG_TIME_THRESHOLD = 2.0   # 平均耗时≤2秒 视为不卡顿
MAX_TIME_THRESHOLD = 5.0   # 最大耗时≤5秒 视为不卡顿
# 输出文件路径（仓库根目录）
OUTPUT_FILE = "result.txt"

# ===================== 核心函数 =====================
def download_remote_result():
    """下载远程的result.txt，返回解析后的播放地址列表 [(名称, 链接), ...]"""
    try:
        print(f"开始下载远程播放列表：{REMOTE_RESULT_URL}")
        response = requests.get(REMOTE_RESULT_URL, timeout=10)
        response.raise_for_status()
        # 按行解析，过滤空行和无效行
        lines = response.text.strip().split('\n')
        play_list = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 匹配 "名称,链接" 格式（兼容中英文逗号、空格分隔）
            match = re.split(r'[,，\s]+', line, maxsplit=1)
            if len(match) == 2:
                name = match[0].strip()
                url = match[1].strip()
                # 仅处理m3u8链接（过滤非直播流地址）
                if url.endswith('.m3u8'):
                    play_list.append((name, url))
        print(f"成功解析到 {len(play_list)} 个m3u8播放地址")
        return play_list
    except Exception as e:
        print(f"下载/解析远程播放列表失败：{str(e)}")
        return []

def parse_m3u8_manually(m3u8_url):
    """手动解析m3u8文件，提取ts分片链接（不依赖m3u8库）"""
    try:
        response = requests.get(m3u8_url, timeout=TIMEOUT)
        response.raise_for_status()
        lines = response.text.strip().split('\n')
        shard_links = []
        base_url = m3u8_url.rsplit('/', 1)[0] + '/'
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.endswith('.ts'):
                full_shard_url = urljoin(base_url, line)
                shard_links.append(full_shard_url)
        if not shard_links:
            raise ValueError("无ts分片链接")
        return shard_links
    except Exception as e:
        print(f"解析m3u8失败 {m3u8_url[:50]}...：{str(e)}")
        return []

def test_shard_download(shard_url):
    """测试单个ts分片下载"""
    try:
        start_time = time.time()
        response = requests.get(shard_url, timeout=TIMEOUT, stream=True)
        response.raise_for_status()
        total_bytes = 0
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                total_bytes += len(chunk)
        end_time = time.time()
        cost_time = round(end_time - start_time, 3)
        if total_bytes == 0:
            return cost_time, False, "分片为空"
        return cost_time, True, "下载成功"
    except requests.exceptions.Timeout:
        return TIMEOUT, False, "超时"
    except requests.exceptions.RequestException as e:
        return 0, False, f"请求失败：{str(e)[:20]}"

def test_stream_smoothness(play_url):
    """测试单个播放地址的流畅度，返回是否卡顿"""
    print(f"\n正在测试：{play_url[:80]}...")
    # 1. 解析m3u8获取分片
    shard_links = parse_m3u8_manually(play_url)
    if not shard_links:
        return False, "无法解析分片"
    # 2. 测试分片下载
    test_shards = shard_links[:TEST_SHARD_COUNT]
    success_count = 0
    cost_times = []
    for shard_url in test_shards:
        cost_time, is_success, _ = test_shard_download(shard_url)
        if is_success:
            success_count += 1
            cost_times.append(cost_time)
    # 3. 计算指标并判定
    total_count = len(test_shards)
    fail_rate = (total_count - success_count) / total_count if total_count > 0 else 1.0
    avg_time = statistics.mean(cost_times) if cost_times else float('inf')
    max_time = max(cost_times) if cost_times else float('inf')
    
    is_smooth = (
        fail_rate <= FAIL_RATE_THRESHOLD and
        avg_time <= AVG_TIME_THRESHOLD and
        max_time <= MAX_TIME_THRESHOLD
    )
    # 输出测试结果
    status = "✅ 流畅" if is_smooth else "❌ 卡顿"
    print(f"{status} | 失败率：{fail_rate:.2%} | 平均耗时：{avg_time:.3f}s | 最大耗时：{max_time:.3f}s")
    return is_smooth, f"失败率：{fail_rate:.2%}，平均耗时：{avg_time:.3f}s"

def main():
    """主流程：下载列表→批量测速→筛选输出"""
    print("="*60)
    print("开始IPTV播放地址测速流程")
    print("="*60)
    
    # 步骤1：下载并解析远程播放列表
    play_list = download_remote_result()
    if not play_list:
        print("❌ 未获取到任何播放地址，流程终止")
        return
    
    # 步骤2：批量测速，筛选不卡顿的地址
    smooth_play_list = []
    total_count = len(play_list)
    for idx, (name, url) in enumerate(play_list, 1):
        print(f"\n[{idx}/{total_count}] 测试：{name}")
        is_smooth, reason = test_stream_smoothness(url)
        if is_smooth:
            smooth_play_list.append((name, url))
    
    # 步骤3：写入筛选结果到仓库根目录
    print(f"\n" + "="*60)
    print(f"测速完成！共检测 {total_count} 个地址，筛选出 {len(smooth_play_list)} 个不卡顿地址")
    print(f"正在写入结果到 {OUTPUT_FILE}")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for name, url in smooth_play_list:
            f.write(f"{name},{url}\n")
    print(f"✅ 结果写入完成！")

if __name__ == "__main__":
    main()
