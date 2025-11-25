import json
import yaml
import shutil
from pathlib import Path
from urllib.parse import quote
from jinja2 import Environment, FileSystemLoader
import argparse
import sys

# map generation deps (used only if network_file configured)
#import pypsa
#import folium
#import numpy as np
#import pandas as pd


def to_web_url(path: Path) -> str:
    """
    Convert a relative path (e.g. assets/...) to a URL-safe string for the browser.
    No file:// prefix; this is meant for static web hosting.
    """
    return quote(path.as_posix(), safe="/:")


def discover_buses(plots_root: Path):
    """
    Buses are inferred as subdirectories of a scenario's plots folder (e.g. UA, MD, RO).
    """
    buses = []
    if not plots_root.exists():
        return buses

    for p in plots_root.iterdir():
        if p.is_dir():
            buses.append(p.name)
    return sorted(buses)


def build_scenarios_data(root: Path, config: dict, assets_root: Path):
    """
    Build nested dict and copy plots into output/assets/...:

    scenarios_data = {
      scenario_key: {
        "name": ...,
        "buses": {
          bus: {
            figure_id: {
              "title": ...,
              "subtitle": ...,
              "url": "assets/<scenario>/<bus>/<file>.png" or None
            },
            ...
          },
          ...
        }
      },
      ...
    }
    """
    scenarios_cfg = config["scenarios"]
    figures_cfg = config["figures"]

    plots_root_cfg = Path(config.get("plots_root", "plots"))
    project = config.get("project", "").strip()

    all_data = {}

    for scen_key, meta in scenarios_cfg.items():
        scen_name = meta.get("name", scen_key)
        scen_folder = meta["folder"]  # under ./plots/ or ./plots/<project>/

        # Build the path to the scenario's plots. If a project is specified in config,
        # expect the layout: <root>/<plots_root>/<project>/<scenario_folder>/...
        if project:
            plots_root = root / plots_root_cfg / project / scen_folder
        else:
            plots_root = root / plots_root_cfg / scen_folder

        if not plots_root.exists():
            raise FileNotFoundError(
                f"Plots directory does not exist for scenario '{scen_key}': {plots_root}"
            )

        print(f"\nScenario '{scen_key}' ('{scen_name}'): using plots_root = {plots_root}")
        buses = discover_buses(plots_root)
        if not buses:
            print(f"  WARNING: no bus subfolders found in {plots_root}")

        scenario_entry = {
            "name": scen_name,
            "buses": {},
        }

        for bus in buses:
            bus_dir = plots_root / bus                 # where we read from
            web_bus_dir = assets_root / scen_key / bus # where we copy to (under ./output/assets)
            web_bus_dir.mkdir(parents=True, exist_ok=True)

            print(f"  Bus '{bus}' in folder: {bus_dir}")
            bus_figs = {}

            for fig_id, fig_meta in figures_cfg.items():
                pattern = fig_meta["pattern"]
                title = fig_meta.get("title", fig_id)
                subtitle_template = fig_meta.get("subtitle_template", "")

                filename = pattern.format(bus=bus)
                fig_path = bus_dir / filename        # original in ./plots/...
                target_path = web_bus_dir / filename # copied under ./output/assets/...
                web_rel_path = Path("assets") / scen_key / bus / filename  # path used in HTML

                if fig_path.exists():
                    shutil.copy2(fig_path, target_path)
                    url = to_web_url(web_rel_path)
                    print(f"    Found {fig_id}: {fig_path} -> {target_path}")
                else:
                    url = None
                    print(f"    Missing {fig_id}: expected {fig_path}")

                bus_figs[fig_id] = {
                    "title": title,
                    "subtitle": subtitle_template.format(bus=bus),
                    "url": url,
                }

            scenario_entry["buses"][bus] = bus_figs

        all_data[scen_key] = scenario_entry

    return all_data


def render_onepager(root: Path, config: dict, scenarios_data, output_root: Path = None):
    # --- Determine text config ---
    # Support three cases:
    # 1) Combined onepager.yaml: the loaded `config` already contains text keys (page/sections/metrics/etc).
    # 2) A per-project text YAML path was provided earlier in config["_text_config_path"] (backwards compatibility).
    # 3) Fallback to config/onepager_text.yaml (older layout).
    if isinstance(config, dict) and ("page" in config or "sections" in config or "metrics" in config):
        text_config = config
    else:
        text_config_path = Path(config.get("_text_config_path", "")) if config.get("_text_config_path") else root / "config" / "onepager_text.yaml"
        if text_config_path.exists():
            with text_config_path.open("r", encoding="utf-8") as f:
                text_config = yaml.safe_load(f)
        else:
            # keep an empty text_config to avoid template hard crash; template should handle missing keys gracefully
            text_config = {}

    text_config_json = json.dumps(text_config, ensure_ascii=False)

    # --- Template/output config ---
    template_cfg = config["template"]
    template_name = template_cfg["name"]
    output_html_name = template_cfg["output_html"]
    output_dir_name = template_cfg.get("output_dir", "output")

    # --- Jinja environment ---
    env = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=True,
    )
    template = env.get_template(template_name)

    html_output = template.render(
        scenarios_json=json.dumps(scenarios_data, ensure_ascii=False),
        text_config=text_config,
        text_config_json=text_config_json,
        default_scenario=config.get("default_scenario", ""),
        default_bus=config.get("default_bus", ""),
    )

    # Resolve final output_root: prefer explicit output_root param (from main),
    # otherwise fall back to template.output_dir under repo root.
    if output_root is None:
        output_root = root / output_dir_name
    output_root.mkdir(parents=True, exist_ok=True)
    out_path = output_root / output_html_name
    print(f"[DEBUG] writing output to: {out_path.resolve()}")
    out_path.write_text(html_output, encoding="utf-8")
    print(f"\nWrote {out_path.resolve()}")
    print(f"[DEBUG] output file size: {out_path.stat().st_size} bytes")


def main():
    root = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Build one-pager from plots")
    parser.add_argument("project", nargs="?", help="Project name (subfolder under config/). If omitted, falls back to config/onepager_config.yaml")
    parser.add_argument("--keep-assets", action="store_true", help="Do not remove existing output assets")
    args = parser.parse_args()

    # Determine onepager config path (single combined YAML: onepager.yaml)
    if args.project:
        project_dir = root / "config" / args.project
        onepager_path = project_dir / "onepager.yaml"
    else:
        project_dir = None
        onepager_path = root / "config" / "onepager.yaml"

    # Fallback support for older separate files: if onepager.yaml not found, fall back to previous behaviour
    if not onepager_path.exists():
        if args.project:
            print(f"Info: combined onepager.yaml not found at {onepager_path}. Falling back to older config files.")
            cfg_path = root / "config" / args.project / "config.yaml"
            text_cfg_path = root / "config" / args.project / "text.yaml"
        else:
            cfg_path = root / "config" / "onepager_config.yaml"
            text_cfg_path = root / "config" / "onepager_text.yaml"
    else:
        cfg_path = None
        text_cfg_path = None

    # --- Add debug diagnostics ---
    try:
        resolved_onepager = onepager_path if 'onepager_path' in locals() else None
        resolved_cfg = cfg_path if 'cfg_path' in locals() else None
    except Exception:
        resolved_onepager = None
        resolved_cfg = None

    print(f"[DEBUG] script root: {root}")
    print(f"[DEBUG] candidate combined onepager path: {resolved_onepager}")
    print(f"[DEBUG] fallback cfg path: {resolved_cfg}")

    if resolved_onepager and resolved_onepager.exists():
        print(f"[DEBUG] Loading combined onepager: {resolved_onepager}")
    else:
        print("[DEBUG] Combined onepager not found; using fallback config file(s)")

    # --- Load configuration (existing logic) ---
    if onepager_path.exists():
        with onepager_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        print(f"[DEBUG] Loaded combined onepager config: {onepager_path}")
    else:
        # fallback to older split files
        if not cfg_path or not cfg_path.exists():
            raise FileNotFoundError(f"Config YAML not found: {cfg_path}")
        with cfg_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        print(f"[DEBUG] Loaded legacy config: {cfg_path} (text path: {config.get('_text_config_path')})")

    # Print resolved template/output targets
    tpl = config.get("template", {}) or {}
    out_dir = tpl.get("output_dir", "output")
    out_html = tpl.get("output_html", "onepager.html")
    print(f"[DEBUG] template.name: {tpl.get('name')}")
    print(f"[DEBUG] expected output_dir: {out_dir}")
    print(f"[DEBUG] expected output_html: {out_html}")
    print(f"[DEBUG] final output_root: {root / out_dir}")

    # compute project subfolder for outputs (use config.project first, then CLI arg)
    tpl = config.get("template", {}) or {}
    out_dir = tpl.get("output_dir", "output")
    project_name = config.get("project") or (args.project if getattr(args, "project", None) else "")
    if project_name:
        output_root = root / out_dir / str(project_name)
    else:
        output_root = root / out_dir

    # assets live inside the chosen output_root (so HTML and assets are colocated)
    assets_root = output_root / tpl.get("assets_subdir", "assets")
    assets_root.mkdir(parents=True, exist_ok=True)

    # Clean old assets (optional; comment out if you want to keep them)
    if assets_root.exists() and not args.keep_assets:
        shutil.rmtree(assets_root)

    assets_root.mkdir(parents=True, exist_ok=True)
    scenarios_data = build_scenarios_data(root=root, config=config, assets_root=assets_root)

    render_onepager(root=root, config=config, scenarios_data=scenarios_data, output_root=output_root)

if __name__ == "__main__":
    import sys, traceback
    # quick sanity prints so the process must emit something to the terminal
    print("[DEBUG] running build_onepager_from_plots.py", file=sys.stderr)
    print(f"[DEBUG] python executable: {sys.executable}", file=sys.stderr)
    print(f"[DEBUG] argv: {sys.argv}", file=sys.stderr)
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
