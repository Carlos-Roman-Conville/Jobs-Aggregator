"""Audit LoRA training metadata for beast/minotaur/bull/furry bias.

For each target LoRA: pull ss_tag_frequency, ss_dataset_dirs, ss_base_model_version,
print top-30 tags, flag any beast-coded terms.
"""
import json
import os
import sys
from pathlib import Path

try:
    from safetensors import safe_open
except ImportError:
    print("safetensors module missing; pip install safetensors", file=sys.stderr)
    sys.exit(1)

LORA_DIR = Path(r"E:/AI Programs/AI art suite/StableDiffusion/stable-diffusion-webui/models/Lora")

TARGETS = [
    "Illustrious_Nat_The_Lich_-_Artist_Style.safetensors",
    "absurdstomachbulge.safetensors",
    "Breeding mount-IL_NAI_PY.safetensors",
    "mating-press-from-above-v3-illustriousxl-lora-nochekaiser.safetensors",
    "full-nelson-illustriousxl-lora-nochekaiser.safetensors",
    "anal-doggystyle-v3-illustriousxl-lora-nochekaiser.safetensors",
    "anal-missionary-from-above-illustriousxl-lora-nochekaiser.safetensors",
    "aqua-about-to-cry-illustriousxl-lora-nochekaiser.safetensors",
    "deepthroat-v2-illustriousxl-lora-nochekaiser.safetensors",
    "excessivecum.safetensors",
    "Sex_slave_training.safetensors",
    "ANAL GAPE.safetensors",
    "Oral gangbang-IL_NAI_PY.safetensors",
    "oral_invitation-000014.safetensors",
    "stomach bulge from oral IL_epoch_10.safetensors",
    "Face_Fucking.safetensors",
    "doublepenetration_r1.safetensors",
    "NM_DAP_ill_v2.safetensors",
    "Dangling_Legs_Spitroast_V1.5-000012.safetensors",
    "anal_cross-section_anyillustriousXLFor_1040_adam_wsf_4320_v1.0.safetensors",
    "xray.safetensors",
]

BEAST_KEYWORDS = [
    "minotaur", "bull", "cow", "ox", "horn", "horns", "horned", "hoof", "hooves",
    "furry", "fur", "anthro", "anthropomorphic", "beast", "monster", "creature",
    "orc", "ogre", "goblin", "troll", "demon", "satyr", "centaur", "naga",
    "muscle bull", "bovine", "snout", "muzzle", "tail", "fangs", "claws",
    "feral", "non-human", "interspecies", "bestiality", "zoophilia",
    "equine", "horse", "donkey", "canine", "dog", "wolf", "fox",
    "scales", "reptile", "lizard", "dragon", "kobold",
    "fur coat", "fur body", "body fur", "thick fur",
    "huge muscles", "veiny muscle", "muscular male", "massive male",
    "dark skin male", "tanned male",  # possible "buff minotaur" coding
]


def classify_tag(tag: str) -> str | None:
    t = tag.lower()
    for kw in BEAST_KEYWORDS:
        if kw in t:
            return kw
    return None


def audit_one(path: Path) -> dict:
    info: dict = {"file": path.name, "exists": path.exists()}
    if not path.exists():
        return info
    try:
        with safe_open(str(path), framework="pt") as f:
            meta = f.metadata() or {}
    except Exception as e:
        info["error"] = f"open failed: {e}"
        return info

    info["base_model"] = meta.get("ss_base_model_version") or meta.get("ss_sd_model_name")
    info["dataset_dirs"] = meta.get("ss_dataset_dirs")
    info["tag_files"] = meta.get("ss_tag_files")
    info["network_module"] = meta.get("ss_network_module")
    info["num_train_images"] = meta.get("ss_num_train_images")
    info["network_dim"] = meta.get("ss_network_dim")

    # tag frequency
    raw_tf = meta.get("ss_tag_frequency")
    tag_total: dict[str, int] = {}
    if raw_tf:
        try:
            parsed = json.loads(raw_tf) if isinstance(raw_tf, str) else raw_tf
            # parsed is {dataset_name: {tag: count}}
            if isinstance(parsed, dict):
                for ds, tags in parsed.items():
                    if isinstance(tags, dict):
                        for k, v in tags.items():
                            tag_total[k] = tag_total.get(k, 0) + int(v)
        except Exception as e:
            info["tag_parse_error"] = str(e)

    sorted_tags = sorted(tag_total.items(), key=lambda kv: -kv[1])
    info["top30_tags"] = sorted_tags[:30]
    info["total_unique_tags"] = len(sorted_tags)

    # Beast scan across ALL tags
    beast_hits: list[tuple[str, int, str]] = []
    for tag, count in sorted_tags:
        kw = classify_tag(tag)
        if kw:
            beast_hits.append((tag, count, kw))
    info["beast_hits"] = beast_hits[:40]
    info["beast_total_unique"] = len(beast_hits)
    info["beast_total_count"] = sum(c for _, c, _ in beast_hits)

    # Also scan dataset dir names
    ds_beast = []
    if info["dataset_dirs"]:
        try:
            ds_parsed = json.loads(info["dataset_dirs"]) if isinstance(info["dataset_dirs"], str) else info["dataset_dirs"]
            for k in (ds_parsed.keys() if isinstance(ds_parsed, dict) else []):
                kw = classify_tag(k)
                if kw:
                    ds_beast.append((k, kw))
        except Exception:
            pass
    info["dataset_beast_hits"] = ds_beast
    return info


def main():
    results = []
    for name in TARGETS:
        p = LORA_DIR / name
        res = audit_one(p)
        results.append(res)
    out_path = Path(r"E:/AI Programs/AI-job-application-pipeline/_lora_beast_audit.json")
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Human-readable summary
    for r in results:
        print("=" * 80)
        print(f"FILE: {r['file']}")
        if not r.get("exists"):
            print("  MISSING")
            continue
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  base_model: {r.get('base_model')}")
        print(f"  num_train_images: {r.get('num_train_images')}")
        print(f"  total_unique_tags: {r.get('total_unique_tags')}")
        print(f"  BEAST tags unique: {r.get('beast_total_unique')}  total count: {r.get('beast_total_count')}")
        if r.get("beast_hits"):
            print("  BEAST HITS:")
            for tag, count, kw in r["beast_hits"][:25]:
                print(f"    [{kw:>10}]  {count:>6}x  {tag}")
        if r.get("dataset_beast_hits"):
            print(f"  DATASET DIR BEAST HITS: {r['dataset_beast_hits']}")
        print("  TOP30 TAGS:")
        for tag, count in (r.get("top30_tags") or [])[:30]:
            print(f"    {count:>6}x  {tag}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
