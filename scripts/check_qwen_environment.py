"""Check checkpoint files and the WSL GPU environment without loading weights."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def check_model_files(model_path: Path) -> dict:
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json",
                 "model.safetensors.index.json"):
        if not (model_path / name).is_file():
            raise ValueError(f"缺少模型文件：{model_path / name}")
    config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "qwen3":
        raise ValueError("当前脚本要求 Qwen3 模型；请检查模型目录。")
    index = json.loads(
        (model_path / "model.safetensors.index.json").read_text(encoding="utf-8")
    )
    shards = sorted(set(index["weight_map"].values()))
    if not shards:
        raise ValueError("模型分片索引为空。")
    for shard in shards:
        if not (model_path / shard).is_file():
            raise ValueError(f"缺少权重分片：{model_path / shard}")
    return {
        "model_path": str(model_path.resolve()),
        "architecture": config.get("architectures"),
        "shards": shards,
        "weights_bytes": sum((model_path / name).stat().st_size for name in shards),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, default=PROJECT_ROOT / "llm/Qwen3")
    parser.add_argument("--files-only", action="store_true")
    args = parser.parse_args()
    try:
        details = check_model_files(args.model_path)
    except (ValueError, KeyError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(details, ensure_ascii=False, indent=2))
    if args.files_only:
        return
    if platform.system() != "Linux" or sys.version_info[:2] != (3, 12):
        raise SystemExit("请在 WSL/Linux 的 Python 3.12 模型环境执行。")
    try:
        import torch
        vllm_version = version("vllm")
    except (ImportError, ModuleNotFoundError) as exc:
        raise SystemExit("请先安装 scripts/requirements-qwen.txt 中的依赖。") from exc
    if not torch.cuda.is_available():
        raise SystemExit("PyTorch 无法访问 CUDA GPU。请检查 WSL 2 和 Windows NVIDIA 驱动。")
    gpu = torch.cuda.get_device_properties(0)
    print(f"vLLM={vllm_version}; PyTorch={torch.__version__}; CUDA={torch.version.cuda}")
    print(f"GPU={gpu.name}; 显存={gpu.total_memory / 1024**3:.2f} GiB; "
          f"compute capability={gpu.major}.{gpu.minor}")
    print("文件和 GPU 检查通过。权重内容、实际显存容量和模型输出仍需启动验证。")


if __name__ == "__main__":
    main()
