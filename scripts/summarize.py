#!/usr/bin/env python3
"""
YouTube Summarizer - Universal YouTube Video Summarization Tool

Usage:
  youtube-summarizer --url "https://youtube.com/watch?v=VIDEO_ID"
  youtube-summarizer --channel "UC_x5XG1OV2P6uZZ5FSM9Ttw" --hours 24
  youtube-summarizer --config channels.json --daily --output /tmp/youtube_summary.json
"""

import os
import sys
import json
import argparse
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from pathlib import Path

# Default config
DEFAULT_MIN_DURATION = 300  # 5 minutes (filter Shorts)
DEFAULT_HOURS_LOOKBACK = 24
DEFAULT_MAX_VIDEOS_PER_CHANNEL = 5
DEFAULT_OUTPUT = "/tmp/youtube_summary.json"

SUMMARY_PROMPT_TEMPLATE = """You are a professional content analyst. Please generate an in-depth, practical summary (at least 300 words) for the following YouTube video transcript.

Video Title: {title}
Channel: {channel}
Duration: {duration}
Transcript: {transcript}

Please output strictly in the following format (no preamble):

### 🎯 Core Problem/Innovation
- Summarize in one sentence what problem the video addresses
- What novel perspectives or technical breakthroughs are presented

### 💡 Key Arguments (detailed, 2-3 sentences each)
1. **Argument 1**: Detailed explanation with specific data, examples, or evidence
2. **Argument 2**: ...
3. **Argument 3**: ...

### 🛠️ Practical Steps (if applicable)
1. Step 1: Specific instructions
2. Step 2: ...

### 💰 Value & Application
- Who would benefit from this content
- How to apply it to real work/life situations

### ⚠️ Considerations
- Risks, limitations, and important notes"""


def get_channel_videos(channel_id: str, hours: int, max_videos: int) -> List[Dict]:
    """Get recent videos from a YouTube channel using yt-dlp"""
    videos = []
    
    # Build channel URL
    if channel_id.startswith("UC") and len(channel_id) == 24:
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
    elif channel_id.startswith("http"):
        url = channel_id.rstrip("/") + "/videos"
    else:
        url = f"https://www.youtube.com/@{channel_id}/videos"
    
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--no-warnings",
                "-J",
                "--playlist-end", str(max_videos * 2),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        
        if result.returncode != 0:
            print(f"⚠️ yt-dlp error for {channel_id}: {result.stderr[:100]}", file=sys.stderr)
            return []
        
        data = json.loads(result.stdout)
        entries = data.get("entries", [])
        
        for entry in entries:
            if not entry:
                continue
            
            video_id = entry.get("id")
            if not video_id:
                continue
            
            # Filter Shorts by duration
            if entry.get("duration") and entry.get("duration") < DEFAULT_MIN_DURATION:
                continue
            
            videos.append({
                "id": video_id,
                "title": entry.get("title", "Unknown"),
                "channel": entry.get("channel", entry.get("uploader", "Unknown")),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "duration_hint": entry.get("duration"),
            })
            
            if len(videos) >= max_videos:
                break
        
    except Exception as e:
        print(f"⚠️ Error fetching channel {channel_id}: {e}", file=sys.stderr)
    
    return videos


def get_video_details(video_id: str) -> Optional[Dict]:
    """Get detailed video metadata using yt-dlp"""
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--no-warnings",
                "-j",
                "--no-download",
                f"https://www.youtube.com/watch?v={video_id}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        
        if result.returncode != 0:
            return None
        
        data = json.loads(result.stdout)
        duration = data.get("duration", 0)
        
        return {
            "duration_seconds": duration,
            "duration": f"{duration // 60}:{duration % 60:02d}",
            "description": data.get("description", "")[:1000],
            "published": data.get("upload_date", ""),
            "view_count": data.get("view_count", 0),
            "like_count": data.get("like_count", 0),
        }
        
    except Exception:
        return None


def get_transcript(video_id: str) -> Optional[str]:
    """Get video transcript using multiple methods to avoid rate limiting"""
    # Method 1: innertube ANDROID client (bypasses rate limits)
    transcript = _get_transcript_innertube_proxy(video_id)
    if transcript:
        return transcript
    
    # Method 2: youtube-transcript-api (fallback, may be rate limited)
    transcript = _get_transcript_ytapi(video_id)
    if transcript:
        return transcript
    
    return None


def _parse_caption_xml(xml_text: str) -> List[str]:
    """Parse YouTube caption XML (supports multiple formats)"""
    import xml.etree.ElementTree as ET
    import html as html_mod
    
    try:
        root = ET.fromstring(xml_text)
        texts = []
        
        # Try <p> tags first (format 3 and format 2)
        for p in root.findall('.//p'):
            # Check for <s> child tags (format 3: word-level)
            words = []
            for s in p.findall('s'):
                if s.text:
                    words.append(html_mod.unescape(s.text.strip()))
            if words:
                texts.append(' '.join(words))
            elif p.text:  # format 2: direct text
                texts.append(html_mod.unescape(p.text.strip()))
        
        # If no <p> found, try <text> tags (format 1)
        if not texts:
            for elem in root.findall('.//text'):
                if elem.text:
                    texts.append(html_mod.unescape(elem.text.strip()))
        
        return texts
    except Exception:
        return []


def _download_caption(url: str) -> Optional[str]:
    """Download caption content directly"""
    try:
        import requests
        r = requests.get(url, timeout=15)
        if r.status_code == 200 and r.text.strip():
            return r.text
    except Exception:
        pass
    
    return None


def _get_transcript_innertube_proxy(video_id: str) -> Optional[str]:
    """Method 1: innertube ANDROID client to download captions"""
    try:
        import innertube
        
        client = innertube.InnerTube('ANDROID')
        data = client.player(video_id=video_id)
        
        if 'captions' not in data:
            return None
        
        caps = data['captions']['playerCaptionsTracklistRenderer']['captionTracks']
        if not caps:
            return None
        
        # Priority: en > zh-Hans > zh > first available
        cap_url = None
        for prefer in ['en', 'zh-Hans', 'zh']:
            for c in caps:
                if c.get('languageCode') == prefer:
                    cap_url = c['baseUrl']
                    break
            if cap_url:
                break
        if not cap_url:
            cap_url = caps[0]['baseUrl']
        
        xml_text = _download_caption(cap_url)
        if not xml_text:
            return None
        
        texts = _parse_caption_xml(xml_text)
        if not texts:
            return None
        
        result = ' '.join(texts).strip()
        return result if len(result) > 50 else None
        
    except Exception:
        return None


def _get_transcript_ytapi(video_id: str) -> Optional[str]:
    """Method 2 (fallback): youtube-transcript-api direct connection"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["zh-Hans", "zh-Hant", "en"])
        transcript = " ".join([item.text if hasattr(item, 'text') else item["text"] for item in fetched])
        return transcript if len(transcript) > 50 else None
        
    except Exception:
        return None


def get_copilot_session_token(gh_token: str) -> Optional[str]:
    """Exchange GitHub token for a Copilot session token"""
    try:
        import requests
        r = requests.get(
            "https://api.github.com/copilot_internal/v2/token",
            headers={
                "Authorization": f"token {gh_token}",
                "Editor-Version": "vscode/1.95.0",
                "User-Agent": "GitHubCopilotChat/0.20.0"
            },
            timeout=10
        )
        if r.status_code == 200:
            return r.json().get("token")
        print(f"⚠️ Failed to get Copilot token: {r.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️ Copilot auth error: {e}", file=sys.stderr)
    return None


def _call_llm(api_url: str, api_key: str, model: str, prompt: str) -> Optional[str]:
    """Call an OpenAI-compatible chat completions endpoint"""
    try:
        import requests
        headers = {
            "Content-Type": "application/json",
            "Editor-Version": "vscode/1.95.0",
            "User-Agent": "GitHubCopilotChat/0.20.0"
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = requests.post(
            api_url,
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000
            },
            timeout=120
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        print(f"⚠️ LLM API error: {response.status_code} {response.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️ LLM call error: {e}", file=sys.stderr)
    return None


def generate_summary(title: str, channel: str, duration: str, transcript: str) -> Optional[str]:
    """Generate summary using LLM API.
    
    Fallback chain:
    1. Environment variables: LLM_API_URL + LLM_API_KEY + LLM_MODEL (custom endpoint)
    2. OPENCLAW_GATEWAY_TOKEN → http://localhost:18789/v1/chat/completions
    3. GITHUB_TOKEN / GH_TOKEN → GitHub Copilot API
    4. POLLINATIONS_API_KEY → Pollinations API
    5. Pollinations free anonymous call (no Authorization header)
    """
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        title=title,
        channel=channel,
        duration=duration,
        transcript=transcript[:8000]
    )

    # --- Level 1: Custom endpoint via environment variables ---
    env_url = os.environ.get("LLM_API_URL")
    env_key = os.environ.get("LLM_API_KEY")
    env_model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    if env_url and env_key:
        print(f"  🔑 Using LLM_API_URL env var: {env_url}", file=sys.stderr)
        result = _call_llm(env_url, env_key, env_model, prompt)
        if result:
            return result

    # --- Level 2: OpenClaw Gateway token ---
    oc_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN")
    if oc_token:
        api_url = env_url or "http://localhost:18789/v1/chat/completions"
        model = env_model
        print(f"  🔑 Using OPENCLAW_GATEWAY_TOKEN → {api_url}", file=sys.stderr)
        result = _call_llm(api_url, oc_token, model, prompt)
        if result:
            return result

    # --- Level 3: GitHub Copilot API via GITHUB_TOKEN or GH_TOKEN ---
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if gh_token:
        print("  🔑 Trying GitHub Copilot API...", file=sys.stderr)
        copilot_token = get_copilot_session_token(gh_token)
        if copilot_token:
            copilot_model = os.environ.get("LLM_MODEL", "claude-haiku-4.5")
            result = _call_llm(
                "https://api.githubcopilot.com/chat/completions",
                copilot_token,
                copilot_model,
                prompt
            )
            if result:
                return result

    # --- Level 4: Pollinations API (with key) ---
    poll_key = os.environ.get("POLLINATIONS_API_KEY")
    if poll_key:
        print("  🔑 Trying Pollinations API (with key)...", file=sys.stderr)
        result = _call_llm(
            "https://gen.pollinations.ai/v1/chat/completions",
            poll_key,
            "openai",
            prompt
        )
        if result:
            return result

    # --- Level 5: Pollinations free anonymous call ---
    print("  🌐 Trying Pollinations free anonymous call...", file=sys.stderr)
    result = _call_llm(
        "https://gen.pollinations.ai/v1/chat/completions",
        "",  # no key
        "openai",
        prompt
    )
    if result:
        return result

    print("⚠️ All LLM backends failed. No summary generated.", file=sys.stderr)
    return None


def process_video(video_id: str, title: str = None, channel: str = None) -> Dict:
    """Process a single video: get details, transcript, and summary"""
    print(f"📹 Processing: {video_id}")
    
    # Get video details
    details = get_video_details(video_id)
    if not details:
        return {
            "video_id": video_id,
            "title": title or "Unknown",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "error": "Failed to fetch video details"
        }
    
    # Get transcript
    transcript = get_transcript(video_id)
    has_transcript = transcript is not None
    
    result = {
        "video_id": video_id,
        "title": title or "Unknown",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "channel": channel or "Unknown",
        "duration": details["duration"],
        "published": details["published"],
        "has_transcript": has_transcript,
        "metadata": {
            "view_count": details.get("view_count", 0),
            "like_count": details.get("like_count", 0),
        }
    }
    
    # Generate summary if transcript available
    if has_transcript:
        print(f"  ✅ Transcript: {len(transcript)} chars")
        summary = generate_summary(title, channel, details["duration"], transcript)
        if summary:
            result["summary"] = summary
            print(f"  ✅ Summary: {len(summary)} chars")
        else:
            result["summary"] = f"⚠️ 摘要生成失败\n\n视频有字幕但 LLM 调用失败。"
    else:
        result["summary"] = f"📺 **需观看获取详细内容**\n\n视频暂无字幕，无法生成详细摘要。\n\n基于标题推测：{title}"
        print(f"  ⚠️ No transcript available")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="YouTube Summarizer")
    parser.add_argument("--url", help="Single video URL")
    parser.add_argument("--channel", help="Channel ID or handle")
    parser.add_argument("--config", help="Config file path (JSON)")
    parser.add_argument("--daily", action="store_true", help="Daily batch mode (requires --config)")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS_LOOKBACK, help="Hours to look back")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON file")
    
    args = parser.parse_args()
    
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": [],
        "stats": {
            "total_videos": 0,
            "with_transcript": 0,
            "without_transcript": 0
        }
    }
    
    # Mode 1: Single video
    if args.url:
        video_id = args.url.split("v=")[-1].split("&")[0]
        result = process_video(video_id)
        results["items"].append(result)
        results["stats"]["total_videos"] = 1
        if result.get("has_transcript"):
            results["stats"]["with_transcript"] = 1
        else:
            results["stats"]["without_transcript"] = 1
    
    # Mode 2: Channel scan
    elif args.channel:
        videos = get_channel_videos(args.channel, args.hours, DEFAULT_MAX_VIDEOS_PER_CHANNEL)
        print(f"📺 Found {len(videos)} videos from channel")
        
        for video in videos:
            result = process_video(video["id"], video["title"], video["channel"])
            results["items"].append(result)
            results["stats"]["total_videos"] += 1
            if result.get("has_transcript"):
                results["stats"]["with_transcript"] += 1
            else:
                results["stats"]["without_transcript"] += 1
    
    # Mode 3: Daily batch (config file)
    elif args.daily and args.config:
        with open(args.config, "r") as f:
            config = json.load(f)
        
        channels = config.get("channels", [])
        hours = config.get("hours_lookback", args.hours)
        max_videos = config.get("max_videos_per_channel", DEFAULT_MAX_VIDEOS_PER_CHANNEL)
        
        print(f"📺 Processing {len(channels)} channels")
        
        for ch in channels:
            channel_id = ch.get("id") or ch.get("url")
            channel_name = ch.get("name", "Unknown")
            
            print(f"\n🔍 Channel: {channel_name}")
            videos = get_channel_videos(channel_id, hours, max_videos)
            print(f"  Found {len(videos)} videos")
            
            for video in videos:
                result = process_video(video["id"], video["title"], channel_name)
                results["items"].append(result)
                results["stats"]["total_videos"] += 1
                if result.get("has_transcript"):
                    results["stats"]["with_transcript"] += 1
                else:
                    results["stats"]["without_transcript"] += 1
    
    else:
        parser.print_help()
        sys.exit(1)
    
    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Output written to: {output_path}")
    print(f"📊 Stats: {results['stats']}")


if __name__ == "__main__":
    main()
