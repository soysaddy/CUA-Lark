import argparse
import base64
import json
import sys
from pathlib import Path

from utils.openai_client import create_openai_client
from config import config


def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test GPT-4o vision with a local image.")
    parser.add_argument("image", type=str, help="本地图片路径")
    parser.add_argument(
        "--prompt",
        type=str,
        default="请描述这张图片里有什么。如果是软件界面，请识别页面内容和关键按钮。",
        help="发送给模型的图片问题",
    )
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        print(json.dumps({"success": False, "error": f"图片不存在: {image_path}"}, ensure_ascii=False, indent=2))
        return 1

    client = create_openai_client()
    if client is None:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": "OpenAI 客户端不可用，请检查 OPENAI_API_KEY / OPENAI_BASE_URL / openai-httpx 版本。",
                    "base_url": config.openai_base_url,
                    "model": config.openai_model,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    base64_image = encode_image(str(image_path))

    try:
        response = client.chat.completions.create(
            model=config.openai_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": args.prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}",
                            },
                        },
                    ],
                }
            ],
            max_tokens=500,
        )
        message = response.choices[0].message.content
        print(
            json.dumps(
                {
                    "success": True,
                    "base_url": config.openai_base_url,
                    "model": config.openai_model,
                    "image": str(image_path),
                    "response": message,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "base_url": config.openai_base_url,
                    "model": config.openai_model,
                    "image": str(image_path),
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
