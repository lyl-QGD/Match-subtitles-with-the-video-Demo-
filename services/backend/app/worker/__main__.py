import argparse
import json
import shutil
from app.config import missing_config

def main():
    parser = argparse.ArgumentParser(description="M1 media worker entry point; no jobs are executed yet.")
    parser.add_argument("--check", action="store_true", help="Report prerequisites without processing media")
    args = parser.parse_args()
    if not args.check:
        parser.error("Only --check is available in M1; import processing starts in M4")
    print(json.dumps({"stage": "M1", "processing_enabled": False,
                      "ffmpeg_available": bool(shutil.which("ffmpeg")),
                      "missing_config": missing_config()}))

if __name__ == "__main__":
    main()
