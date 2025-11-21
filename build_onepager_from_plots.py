import json
import yaml
import shutil
from pathlib import Path
from urllib.parse import quote
from jinja2 import Environment, FileSystemLoader


def to_web_url(path: Path) -> str:
    """
    Convert a relative path (e.g. assets/...) to a URL-safe string for the browser.
    No file:// prefix; this is meant for static web hosting.
    """
    return quote(path.as_posix(), safe="/:")


def discover_buses(plots_root: Path):
    """
    Buses are inferred as subdirectories of plots_root (e.g. UA, MD, RO).
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

    all_data = {}

    for scen_key, meta in scenarios_cfg.items():
        scen_name = meta.get("name", scen_key)
        scen_folder = meta["folder"]  # under ./plots/

        plots_root = root / "plots" / scen_folder
        if not plots_root.exists():
            raise FileNotFoundError(
                f"plots_root does not exist for scenario '{scen_key}': {plots_root}"
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
                fig_path = bus_dir / filename        # local original in ./plots/...
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


def render_onepager(root: Path, config: dict, scenarios_data):
    # --- Load text configuration from YAML ---
    text_config_path = root / "config" / "onepager_text.yaml"
    if not text_config_path.exists():
        raise FileNotFoundError(f"Text config YAML not found: {text_config_path}")

    with text_config_path.open("r", encoding="utf-8") as f:
        text_config = yaml.safe_load(f)

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
    )

    output_root = root / output_dir_name
    output_root.mkdir(exist_ok=True)

    out_path = output_root / output_html_name
    out_path.write_text(html_output, encoding="utf-8")
    print(f"\nWrote {out_path.resolve()}")


def main():
    root = Path(__file__).resolve().parent

    # --- Load structural/config YAML ---
    config_path = root / "config" / "onepager_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config YAML not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    template_cfg = config["template"]
    output_dir_name = template_cfg.get("output_dir", "output")
    assets_subdir = template_cfg.get("assets_subdir", "assets")

    output_root = root / output_dir_name
    assets_root = output_root / assets_subdir

    # Clean old assets (optional; comment out if you want to keep them)
    if assets_root.exists():
        shutil.rmtree(assets_root)

    assets_root.mkdir(parents=True, exist_ok=True)

    scenarios_data = build_scenarios_data(root=root, config=config, assets_root=assets_root)
    render_onepager(root=root, config=config, scenarios_data=scenarios_data)


if __name__ == "__main__":
    main()
