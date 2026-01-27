import requests
import time
import statistics
from urllib.parse import urljoin
from datetime import datetime

# ===================== 核心配置（无需修改）=====================
# 原始txt的RAW地址（跳过GitHub blob页面，直接获取纯文本）
RAW_TXT_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/GSD-3726/IPTV/master/output/result.txt"
OUTPUT_FILE = "result.txt"  # 输出到仓库根目录，文件名与原始一致
# 测速配置（平衡海外服务器速度/准确性）
TEST_SHARD_COUNT = 3  # m3u8分片测试数量
TIMEOUT = 5           # 单次请求超时时间（秒）
# 卡顿判定阈值（适配海外服务器访问国内源）
FAIL_RATE_THRESHOLD = 0.1   # 失败率≤10%
AVG_TIME_THRESHOLD = 2.5    # 平均下载耗时≤2.5秒
MAX_TIME_THRESHOLD = 6.0    # 最大下载耗时≤6秒
# 支持的协议（UDP无法通过HTTP测试，直接过滤）
SUPPORTED_PROTOCOLS = ("http://", "https://")

# ===================== 工具函数 =====================
def download_original_txt():
    """下载原始txt文件，返回【原始行列表】（保留所有表情/符号/空格）"""
    try:
        # 模拟浏览器请求，避免被拦截
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(RAW_TXT_URL, headers=headers, timeout=20)
        response.raise_for_status()  # 抛出HTTP错误
        response.encoding = "utf-8"  # 强制UTF-8，保证中文/表情无乱码
        # 按行分割，保留原始换行符外的所有格式（过滤纯空行）
        original_lines = [line.rstrip('\n') for line in response.text.splitlines() if line.strip()]
        print(f"✅ 成功下载原始文件，共{len(original_lines)}行（保留所有原始格式）")
        return original_lines
    except Exception as e:
        print(f"❌ 下载原始txt失败：{str(e)}")
        return []

def parse_m3u8_shards(m3u8_url):
    """手动解析m3u8文件，提取ts分片链接（不依赖第三方库）"""
    try:
        response = requests.get(m3u8_url, timeout=TIMEOUT)
        response.raise_for_status()
        base_url = m3u8_url.rsplit('/', 1)[0] + '/' if '/' in m3u8_url else ''
        shard_links = []
        for line in response.text.splitlines():
            line = line.strip()
            # 跳过注释行和空行，只保留ts分片
            if line and not line.startswith('#') and line.endswith('.ts'):
                shard_links.append(urljoin(base_url, line))
        return shard_links if shard_links else None
    except Exception:
        return None

def test_stream_smoothness(play_url):
    """测试单个播放地址是否卡顿（适配m3u8/flv）"""
    # 过滤不支持的协议（UDP等）
    if not play_url.startswith(SUPPORTED_PROTOCOLS):
        return False
    
    # 测试m3u8格式
    if play_url.endswith('.m3u8'):
        shard_links = parse_m3u8_shards(play_url)
        if not shard_links:
            return False
        success_count = 0
        cost_times = []
        # 测试前N个分片
        for shard_url in shard_links[:TEST_SHARD_COUNT]:
            try:
                start_time = time.time()
                # 流式下载前50KB，验证可用性
                response = requests.get(shard_url, timeout=TIMEOUT, stream=True)
                response.raise_for_status()
                total_bytes = 0
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        total_bytes += len(chunk)
                        if total_bytes >= 51200:  # 下载50KB后停止
                            break
                cost_time = round(time.time() - start_time, 3)
                # 至少下载10KB视为成功
                if total_bytes >= 10240:
                    success_count += 1
                    cost_times.append(cost_time)
            except Exception:
                continue
        # 无成功分片则判定卡顿
        if not cost_times:
            return False
        # 计算判定指标
        fail_rate = (TEST_SHARD_COUNT - success_count) / TEST_SHARD_COUNT
        avg_time = statistics.mean(cost_times)
        max_time = max(cost_times)
        # 判定是否流畅
        return (fail_rate <= FAIL_RATE_THRESHOLD and
                avg_time <= AVG_TIME_THRESHOLD and
                max_time <= MAX_TIME_THRESHOLD)
    
    # 测试flv格式
    elif play_url.endswith('.flv'):
        try:
            start_time = time.time()
            response = requests.get(play_url, timeout=TIMEOUT, stream=True)
            response.raise_for_status()
            # 下载前100KB验证
            total_bytes = 0
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    total_bytes += len(chunk)
                    if total_bytes >= 102400:
                        break
            cost_time = round(time.time() - start_time, 3)
            # 至少下载10KB且耗时≤最大阈值视为流畅
            return total_bytes >= 10240 and cost_time <= MAX_TIME_THRESHOLD
        except Exception:
            return False
    
    # 其他格式（非m3u8/flv）直接判定为卡顿
    else:
        return False

# ===================== 主逻辑：严格按原始格式处理 =====================
def main():
    print("="*70)
    print(f"IPTV源测速开始 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    # 1. 下载原始txt，获取所有原始行（保留格式）
    original_lines = download_original_txt()
    if not original_lines:
        print("❌ 无原始数据，终止流程")
        return
    
    # 2. 处理每一行，严格保留原始格式
    output_lines = []
    total_test_url = 0
    smooth_url_count = 0
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for line in original_lines:
        # 处理【更新时间行】：保留🕘️+#genre#，仅替换时间
        if line.startswith("🕘️") and "#genre#" in line:
            output_line = f"🕘️{current_datetime},#genre#"
            output_lines.append(output_line)
            print(f"📅 更新时间行：{output_line}")
        
        # 处理【分类行】：如📺央视频道,#genre#，完全保留原始格式
        elif "#genre#" in line and not line.startswith("🕘️"):
            output_lines.append(line)
            print(f"\n📋 分类行（保留）：{line}")
        
        # 处理【播放地址行】：名称,链接 格式，测速后筛选
        else:
            if "," not in line:
                continue  # 非名称+链接格式，跳过（避免无效行）
            # 仅分割第一个逗号（防止链接含逗号导致解析错误）
            name_part, url_part = line.split(",", 1)
            name = name_part.strip()
            play_url = url_part.strip()
            total_test_url += 1
            print(f"[{total_test_url}] 测试：{name} | {play_url[:60]}...", end=" ")
            
            # 测速并判定是否保留
            if test_stream_smoothness(play_url):
                smooth_url_count += 1
                output_lines.append(line)  # 完全保留原始地址行格式
                print("✅ 流畅（保留）")
            else:
                print("❌ 卡顿/不可用（跳过）")
    
    # 3. 写入结果到仓库根目录的result.txt（严格按原始格式）
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        # 按原始行的换行格式写入（每行一个条目）
        f.write("\n".join(output_lines))
    
    # 4. 输出统计信息
    print("="*70)
    print(f"✅ 测速完成 | 总测试地址：{total_test_url} | 保留流畅地址：{smooth_url_count}")
    print(f"📄 结果文件已生成：仓库根目录/{OUTPUT_FILE}（格式与原始txt完全一致）")
    print("="*70)

if __name__ == "__main__":
    main()
