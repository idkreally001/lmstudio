#!/usr/bin/env python3
"""CLI utility to validate and inspect config.json and prompts.yaml."""
import argparse
import json
import sys
import os

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_yaml(path):
    try:
        import yaml
    except ImportError:
        print("[!] PyYAML not installed. Run: pip install pyyaml")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

REQUIRED_CONFIG_KEYS = ["max_tokens", "max_iterations", "temperature", "hard_limit", "timeout"]

def validate_config(cfg):
    errors = []
    for key in REQUIRED_CONFIG_KEYS:
        if key not in cfg:
            errors.append(f"Missing required key: '{key}'")
    if "max_tokens" in cfg and not isinstance(cfg["max_tokens"], int):
        errors.append(f"'max_tokens' must be an integer, got {type(cfg['max_tokens']).__name__}")
    if "temperature" in cfg and not isinstance(cfg["temperature"], (int, float)):
        errors.append(f"'temperature' must be a number, got {type(cfg['temperature']).__name__}")
    if "audio_cache" in cfg:
        ac = cfg["audio_cache"]
        if not isinstance(ac, dict):
            errors.append("'audio_cache' must be an object")
        else:
            if "max_files" in ac and not isinstance(ac["max_files"], int):
                errors.append("'audio_cache.max_files' must be an integer")
            if "max_size_mb" in ac and not isinstance(ac["max_size_mb"], (int, float)):
                errors.append("'audio_cache.max_size_mb' must be a number")
    return errors

def validate_prompts(prompts):
    errors = []
    if "system_prompt" not in prompts:
        errors.append("Missing 'system_prompt' key")
    elif not isinstance(prompts["system_prompt"], list):
        errors.append("'system_prompt' must be a list of strings")
    else:
        for i, item in enumerate(prompts["system_prompt"]):
            if not isinstance(item, str):
                errors.append(f"system_prompt[{i}] must be a string, got {type(item).__name__}")
    return errors

def main():
    parser = argparse.ArgumentParser(description="Validate and inspect AI Agent configuration files.")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--prompts", default="prompts.yaml", help="Path to prompts.yaml")
    parser.add_argument("--print", dest="pretty", action="store_true", help="Pretty-print the loaded config")
    parser.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="Set a top-level config key (writes to config.json)")
    args = parser.parse_args()

    # Validate config
    print(f"\n--- Validating {args.config} ---")
    try:
        cfg = load_json(args.config)
        errors = validate_config(cfg)
        if errors:
            for e in errors:
                print(f"  [ERROR] {e}")
        else:
            print("  [OK] config.json is valid.")
        if args.pretty:
            print(json.dumps(cfg, indent=2))
    except FileNotFoundError:
        print(f"  [ERROR] {args.config} not found.")
    except json.JSONDecodeError as e:
        print(f"  [ERROR] Invalid JSON: {e}")

    # Validate prompts
    if os.path.exists(args.prompts):
        print(f"\n--- Validating {args.prompts} ---")
        try:
            prompts = load_yaml(args.prompts)
            errors = validate_prompts(prompts)
            if errors:
                for e in errors:
                    print(f"  [ERROR] {e}")
            else:
                print("  [OK] prompts.yaml is valid.")
            if args.pretty:
                print(json.dumps(prompts, indent=2, default=str))
        except Exception as e:
            print(f"  [ERROR] Failed to parse prompts: {e}")
    else:
        print(f"\n[SKIP] {args.prompts} not found (optional).")

    # Set a config key
    if args.set:
        key, value = args.set
        try:
            cfg = load_json(args.config)
        except Exception:
            cfg = {}
        # Try to parse value as JSON (for numbers, bools, objects)
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed = value
        cfg[key] = parsed
        with open(args.config, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        print(f"\n[SET] {key} = {parsed}")

if __name__ == "__main__":
    main()
