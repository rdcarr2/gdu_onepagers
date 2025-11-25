import argparse
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import yaml
from IPython.display import display
from pypsa import Network


def main():
    # --------------------------------------------------------------
    # Parse command-line arguments
    # --------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="Build an interactive network map for a given project."
    )
    parser.add_argument(
        "project",
        help="Project name, matching the folder config/<project>/onepager.yaml "
             "(e.g. 'load_shedding_update')."
    )
    args = parser.parse_args()
    project_arg = args.project

    # --------------------------------------------------------------
    # Paths & config
    # --------------------------------------------------------------
    BASE_DIR = Path(__file__).resolve().parent

    # Config path: config/<project>/onepager.yaml
    cfg_path = BASE_DIR / "config" / project_arg / "onepager.yaml"

    if not cfg_path.is_file():
        raise FileNotFoundError(
            f"Config file not found at {cfg_path}. "
            f"Expected path: config/{project_arg}/onepager.yaml"
        )

    with cfg_path.open() as f:
        cfg = yaml.safe_load(f) or {}

    # 'project' inside YAML (optional but nice consistency check)
    project_name = cfg.get("project", project_arg)

    # Network file from YAML
    net_spec = cfg.get("network_file")
    if not net_spec:
        raise ValueError(
            f"'network_file' key missing in {cfg_path}. "
            "Please add e.g. network_file: 'network_files/...</...>.nc'"
        )

    net_path = Path(net_spec)
    if not net_path.is_absolute():
        net_path = BASE_DIR / net_path

    if not net_path.is_file():
        raise FileNotFoundError(
            f"Network file not found at {net_path}. "
            f"Configured in {cfg_path} as network_file: {net_spec}"
        )

    print(f"Using project: {project_name}")
    print(f"Reading config: {cfg_path}")
    print(f"Loading network: {net_path}")

    # --------------------------------------------------------------
    # Load network
    # --------------------------------------------------------------
    n = Network(str(net_path))

    # --- timestep hours (robust) ---
    dt_hours = 1.0
    try:
        snaps = n.snapshots
        if len(snaps) > 1 and isinstance(snaps, pd.DatetimeIndex):
            diffs = snaps.to_series().diff().dropna()
            dt_hours = diffs.dt.total_seconds().median() / 3600.0
    except Exception:
        dt_hours = 1.0

    # --- pick buses with 2-letter names ---
    selected_buses = [b for b in n.buses.index if len(str(b)) == 2]
    coords = n.buses.loc[selected_buses]

    # --- compute total demand per bus (TWh) ---
    if hasattr(n, "loads_t") and hasattr(n.loads_t, "p_set"):
        energy_mwh_per_load = n.loads_t.p_set.sum(axis=0) * dt_hours
        load_bus_map = n.loads["bus"]
        energy_mwh_per_bus = energy_mwh_per_load.groupby(load_bus_map).sum()
    else:
        energy_mwh_per_bus = pd.Series(dtype=float)

    energy_twh_per_bus = (
        energy_mwh_per_bus / 1e6
    ).reindex(index=n.buses.index).fillna(0.0)

    # --- compute installed capacities per bus (GW) from generators + storage_units ---
    gen_cap_mw = pd.Series(0.0, index=n.buses.index)

    if len(n.generators) > 0:
        gen_caps = n.generators.get("p_nom", pd.Series(0.0)).fillna(0.0)
        gen_caps_by_bus = gen_caps.groupby(n.generators["bus"]).sum()
        gen_cap_mw = gen_cap_mw.add(gen_caps_by_bus, fill_value=0.0)

    if len(n.storage_units) > 0:
        stor_caps = n.storage_units.get("p_nom", pd.Series(0.0)).fillna(0.0)
        stor_caps_by_bus = stor_caps.groupby(n.storage_units["bus"]).sum()
        gen_cap_mw = gen_cap_mw.add(stor_caps_by_bus, fill_value=0.0)

    gen_cap_gw = (gen_cap_mw / 1000.0).reindex(index=n.buses.index).fillna(0.0)

    # --- installed capacities aggregated by type (for popup on click) ---
    def _type_col(df):
        for c in ("carrier", "type", "technology"):
            if c in df.columns:
                return c
        return None

    gen_type_col = _type_col(n.generators) if len(n.generators) > 0 else None
    stor_type_col = _type_col(n.storage_units) if len(n.storage_units) > 0 else None

    # generators by (bus, type) in MW
    if len(n.generators) > 0 and gen_type_col is not None:
        gen_by_bus_type = (
            n.generators.fillna({"p_nom": 0.0})
            .groupby(["bus", gen_type_col])["p_nom"]
            .sum()
        )
    else:
        # fallback: treat all generators as a single group 'generator'
        gen_by_bus_type = (
            n.generators.get("p_nom", pd.Series(0.0))
            .groupby(n.generators.get("bus", pd.Series()))
            .sum()
        )
        gen_by_bus_type.index = pd.MultiIndex.from_tuples(
            [(b, "generator") for b in gen_by_bus_type.index],
            names=["bus", "type"],
        )

    # storage by (bus, type) in MW
    if len(n.storage_units) > 0 and stor_type_col is not None:
        stor_by_bus_type = (
            n.storage_units.fillna({"p_nom": 0.0})
            .groupby(["bus", stor_type_col])["p_nom"]
            .sum()
        )
    else:
        stor_by_bus_type = (
            n.storage_units.get("p_nom", pd.Series(0.0))
            .groupby(n.storage_units.get("bus", pd.Series()))
            .sum()
        )
        stor_by_bus_type.index = pd.MultiIndex.from_tuples(
            [(b, "storage") for b in stor_by_bus_type.index],
            names=["bus", "type"],
        )

    # helper to extract per-bus dict of type -> GW
    def _types_for_bus(series_multiindex, bus):
        out = {}
        try:
            s = series_multiindex.xs(bus, level="bus")
            for t, val in s.items():
                out[str(t)] = float(val) / 1000.0
        except KeyError:
            pass
        return out

    # --- prepare bus properties (for tooltip + popup) ---
    bus_props = {}
    for b in selected_buses:
        x = float(n.buses.at[b, "x"])
        y = float(n.buses.at[b, "y"])
        demand_twh = round(float(energy_twh_per_bus.get(b, 0.0)), 6)
        installed_gw = round(float(gen_cap_gw.get(b, 0.0)), 6)
        gen_types = _types_for_bus(gen_by_bus_type, b)
        stor_types = _types_for_bus(stor_by_bus_type, b)
        bus_props[b] = {
            "x": x,
            "y": y,
            "demand_twh": demand_twh,
            "installed_gw": installed_gw,
            "gen_types_gw": gen_types,
            "stor_types_gw": stor_types,
        }

    # --- select lines/links connecting only two selected buses ---
    sel_lines = n.lines[
        n.lines["bus0"].isin(selected_buses)
        & n.lines["bus1"].isin(selected_buses)
    ].copy()

    sel_links = n.links[
        n.links["bus0"].isin(selected_buses)
        & n.links["bus1"].isin(selected_buses)
    ].copy()

    # suppress lines that have a link between same unordered pair (prioritise links)
    link_pairs = {
        tuple(sorted([r.bus0, r.bus1])) for _, r in sel_links.iterrows()
    }
    if not sel_lines.empty:
        sel_lines["pair"] = sel_lines.apply(
            lambda r: tuple(sorted([r.bus0, r.bus1])),
            axis=1,
        )
        sel_lines = sel_lines[~sel_lines["pair"].isin(link_pairs)]

    # --- helper to format capacity (MW) ---
    def cap_mw_from_line(r):
        if not pd.isna(r.get("s_nom", np.nan)):
            return float(r.get("s_nom"))
        if "capacity" in r:
            return float(r.get("capacity", np.nan))
        return np.nan

    def cap_mw_from_link(r):
        return float(r.get("p_nom", np.nan)) if not pd.isna(r.get("p_nom", np.nan)) else np.nan

    # --- build folium map centered on mean coords ---
    if len(coords) > 0:
        center_lat = coords.y.mean()
        center_lon = coords.x.mean()
    else:
        center_lat, center_lon = 49.0, 31.0  # fallback

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=6,
        tiles="cartodbpositron",
    )

    # draw lines (grey)
    for _, row in sel_lines.iterrows():
        b0 = row.bus0
        b1 = row.bus1
        x0, y0 = float(n.buses.at[b0, "x"]), float(n.buses.at[b0, "y"])
        x1, y1 = float(n.buses.at[b1, "x"]), float(n.buses.at[b1, "y"])
        capacity = cap_mw_from_line(row)

        line_tooltip = (
            f"Line {row.name if hasattr(row, 'name') else ''}<br>"
            f"Bus0: {b0}<br>"
            f"Bus1: {b1}<br>"
            f"Transfer capacity: {capacity if not np.isnan(capacity) else 'n/a'} MW"
        )

        folium.PolyLine(
            locations=[[y0, x0], [y1, x1]],
            color="gray",
            weight=2,
            opacity=0.8,
            tooltip=folium.Tooltip(line_tooltip, sticky=True),
        ).add_to(m)

    # draw links (orange, same width)
    for _, row in sel_links.iterrows():
        b0 = row.bus0
        b1 = row.bus1
        x0, y0 = float(n.buses.at[b0, "x"]), float(n.buses.at[b0, "y"])
        x1, y1 = float(n.buses.at[b1, "x"]), float(n.buses.at[b1, "y"])
        capacity = cap_mw_from_link(row)

        link_tooltip = (
            f"Link {row.name if hasattr(row, 'name') else ''}<br>"
            f"Bus0: {b0}<br>"
            f"Bus1: {b1}<br>"
            f"Transfer capacity: {capacity if not np.isnan(capacity) else 'n/a'} MW"
        )

        folium.PolyLine(
            locations=[[y0, x0], [y1, x1]],
            color="orange",
            weight=2,
            opacity=0.9,
            tooltip=folium.Tooltip(link_tooltip, sticky=True),
        ).add_to(m)

    # draw buses as circle markers with popups
    for b, p in bus_props.items():
        hover_txt = (
            f"{b}:<br>"
            f"Load: {p['demand_twh']:.6f} TWh<br>"
            f"Total installed capacities: {p['installed_gw']:.6f} GW"
        )

        parts = [
            f"<b>{b}</b>",
            f"Coordinates: {p['x']:.4f}, {p['y']:.4f}",
            f"Total demand: {p['demand_twh']:.6f} TWh",
            f"Total installed capacity: {p['installed_gw']:.6f} GW",
            "<hr>",
            "<b>Installed capacities by type (GW)</b>",
        ]

        # generators
        parts.append("<u>Generators</u>")
        if p["gen_types_gw"]:
            for t, gw in sorted(p["gen_types_gw"].items()):
                parts.append(f"{t}: {gw:.6f} GW")
        else:
            parts.append("none")

        # storage
        parts.append("<u>Storage</u>")
        if p["stor_types_gw"]:
            for t, gw in sorted(p["stor_types_gw"].items()):
                parts.append(f"{t}: {gw:.6f} GW")
        else:
            parts.append("none")

        popup_html = "<br>".join(parts)

        folium.CircleMarker(
            location=[p["y"], p["x"]],
            radius=5,
            color="red",
            fill=True,
            fill_color="red",
            popup=folium.Popup(popup_html, max_width=350),
            tooltip=folium.Tooltip(hover_txt, sticky=True),
        ).add_to(m)

    # --------------------------------------------------------------
    # Save and display
    # --------------------------------------------------------------
    out_dir = BASE_DIR / "output" / project_name
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / "network_map.html"
    m.save(out_file)
    print(f"Saved map to: {out_file}")

    # Show in notebook contexts (harmless in CLI)
    try:
        display(m)
    except Exception:
        pass


if __name__ == "__main__":
    main()
