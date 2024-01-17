import os
import json
import logging
import shutil
import sqlite3
import csv
import pandas as pd
from logging import FileHandler
from logging import StreamHandler
from pathlib import Path
from string import ascii_uppercase
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from gcbmwalltowall.configuration.gcbmconfigurer import GCBMConfigurer
from mojadata.boundingbox import BoundingBox
from mojadata.gdaltiler2d import GdalTiler2D
from mojadata.layer.rasterlayer import RasterLayer
from mojadata.layer.gcbm.disturbancelayer import DisturbanceLayer
from mojadata.layer.gcbm.transitionrulemanager import SharedTransitionRuleManager
from mojadata.config import *
from mojadata.cleanup import cleanup
from mojadata.util import gdal
from mojadata.util.gdalhelper import GDALHelper
from mojadata.util.gdal_calc import Calc

def load_fuel_bed_tables(input_db_path):
    this_script_path = os.path.abspath(os.path.dirname(__file__))
    fuel_bed_lut_path = rf"{this_script_path}\..\04_logs\Supplementary_logfiles\2023_07_12_FutureFire_Equations.xlsx"
    
    aidb_ecoboundary_lookup = {
         4: "Taiga Plains",
         5: "Taiga Shield West",
         6: "Boreal Shield West",
         7: "Atlantic Maritime",
         8: "Mixedwood Plains",
         9: "Boreal Plains",
        10: "Subhumid Prairies",
        11: "Taiga Cordillera",
        12: "Boreal Cordillera",
        13: "Pacific Maritime",
        14: "Montane Cordillera",
        15: "Hudson Plains",
        16: "Taiga Shield East",
        17: "Boreal Shield East",
        18: "Semiarid Prairies"
    }
    
    with sqlite3.connect(input_db_path) as gcbm_input_db:
        gcbm_input_db.execute(
            """
            CREATE TABLE IF NOT EXISTS fuel_bed_type (
                id INTEGER PRIMARY KEY,
                name VARCHAR UNIQUE NOT NULL,
                description VARCHAR NOT NULL,
                cbh INTEGER NOT NULL,
                cfl REAL NOT NULL,
                ros_a INTEGER NOT NULL,
                ros_b REAL NOT NULL,
                ros_c0 REAL NOT NULL,
                q REAL NOT NULL,
                bui_avg INTEGER NOT NULL)
            """)
    
        gcbm_input_db.execute(
            """
            CREATE TABLE IF NOT EXISTS fuel_bed (
                id INTEGER PRIMARY KEY,
                species_id INTEGER NOT NULL,
                eco_boundary_id INTEGER NOT NULL,
                fuel_bed_type_id INTEGER NOT NULL,
                min_age INTEGER NOT NULL,
                max_age INTEGER NOT NULL,
                FOREIGN KEY (species_id) REFERENCES species (id),
                FOREIGN KEY (eco_boundary_id) REFERENCES eco_boundary (id),
                FOREIGN KEY (fuel_bed_type_id) REFERENCES fuel_bed_type (id))
            """)
            
        gcbm_input_db.execute(
            """
            INSERT INTO fuel_bed_type (name, description, cbh, cfl, ros_a, ros_b, ros_c0, q, bui_avg)
            VALUES
                ('C1', 'Spruce / Lichen Woodland', 2, 0.75, 90, 0.0649, 4.5, 0.9, 72),
                ('C2', 'Boreal Spruce', 3, 0.8, 110, 0.0282, 1.5, 0.7, 64),
                ('C3', 'Mature Jack or Lodgepole Pine', 8, 1.15, 110, 0.0444, 3.0, 0.75, 62),
                ('C4', 'Immature Jack or Lodgepole Pine', 4, 1.2, 110, 0.0293, 1.5, 0.8, 66),
                ('C5', 'Red and White Pine', 18, 1.2, 30, 0.0697, 4.0, 0.8, 56),
                ('C6', 'Conifer Plantation', 7, 1.8, 30, 0.08, 3.0, 0.8, 62),
                ('C7', 'Ponderosa Pine / Douglas-Fir', 10, 0.5, 45, 0.0305, 2.0, 0.85, 106),
                ('D1', 'Leafless Aspen', 0, 0, 30, 0.0232, 1.6, 0.9, 32),
                ('S1', 'Jack or Lodgepole Pine Slash', 0, 0, 75, 0.0297, 1.3, 0.75, 38),
                ('S2', 'White Spruce / Balsam Slash', 0, 0, 40, 0.0438, 1.7, 0.75, 63),
                ('S3', 'Coastal Cedar / Hemlock / Douglas-Fir Slash', 0, 0, 55, 0.0829, 3.2, 0.75, 31),
                ('O1', 'Grass', 0, 0, 220, 0.033, 1.55, 1, 1),
                ('M1', 'Boreal Mixedwood / Leafless', 6, 0.8, 0, 0, 0, 0.8, 50),
                ('M2', 'Boreal Mixedwood / Green', 6, 0.8, 0, 0, 0, 0.8, 50),
                ('M3', 'Dead Balsam Fir Mixedwood - Leafless', 6, 0.8, 120, 0.0572, 1.4, 0.8, 50),
                ('M4', 'Dead Balsam Fir Mixedwood - Green', 6, 0.8, 100, 0.0404, 1.48, 0.8, 50)
            """)
    
        for _, row in pd.read_excel(fuel_bed_lut_path, sheet_name="LUT_for_scripts").iterrows():
            row_data = row.to_dict()
            
            ecoboundaries = [
                aidb_ecoboundary_lookup[int(id)] for id in str(row_data["ecoboundaries"]).split(",")
            ]
            
            for species in row_data["species"].split(","):
                for ecoboundary in ecoboundaries:
                    insert_data = [
                        int(row_data["age_min"]), int(row_data["age_max"]),
                        species, ecoboundary, row_data["fuel_bed"]
                    ]
                    
                    gcbm_input_db.execute(
                        """
                        INSERT INTO fuel_bed (species_id, eco_boundary_id, fuel_bed_type_id, min_age, max_age)
                        SELECT s.id, e.id, f.id, ? AS min_age, ? AS max_age
                        FROM species s, eco_boundary e, fuel_bed_type f
                        WHERE LOWER(s.name) = LOWER(?)
                            AND e.name = ?
                            AND f.name = ?
                        """, insert_data
                    )
                    
                    if gcbm_input_db.execute("SELECT CHANGES()").fetchone()[0] != 1:
                        sys.exit(f"Failed to insert row: {insert_data}")
        
        gcbm_input_db.commit()

def get_max_bounds(layer_paths):
    all_x = []
    all_y = []
    for layer_path in layer_paths:
        info = GDALHelper.info(str(layer_path))
        bounds = info["cornerCoordinates"]
        all_x.append(bounds["upperLeft"][0])
        all_x.append(bounds["lowerRight"][0])
        all_y.append(bounds["lowerRight"][1])
        all_y.append(bounds["upperLeft"][1])

    return (min(all_x), max(all_y), max(all_x), min(all_y))

def get_nodata_value(layer_path):
    ds = gdal.Open(str(layer_path))
    return ds.GetRasterBand(1).GetNoDataValue()

def create_common_mask(inventory_paths, output_dir):
    age_output_layers = [
        str(next(inventory_path.rglob(r"age_*.tif")).absolute())
        for inventory_path in inventory_paths
    ]
    
    with TemporaryDirectory() as tmp_dir:
        # Sometimes the GCBM output bounds shrinks because of non-simulated pixels,
        # but Calc needs the extents to match.
        clipped_age_output_layers = []
        gcbm_bounds = get_max_bounds(age_output_layers)
        for i, original_age_layer in enumerate(age_output_layers):
            clipped_layer = str(Path(tmp_dir).joinpath(f"clipped_age_{i}.tif"))
            clipped_age_output_layers.append(clipped_layer)
            gdal.Warp(clipped_layer, original_age_layer,
                      outputBounds=gcbm_bounds,
                      warpMemoryLimit=GDAL_MEMORY_LIMIT,
                      options=GDAL_WARP_OPTIONS.copy(),
                      creationOptions=GDAL_WARP_CREATION_OPTIONS)
    
        layer_codes = dict(zip(
            ascii_uppercase[:len(clipped_age_output_layers)],
            clipped_age_output_layers
        ))

        calc = " & ".join((
            f"({layer_code} != {get_nodata_value(layer_path)})"
            for layer_code, layer_path in layer_codes.items()
        ))
        
        output_layer = output_dir.joinpath("common_inventory_mask.tif")
        Calc(calc, str(output_layer), 0, creation_options=["TILED=YES", "COMPRESS=DEFLATE"],
             overwrite=True, quiet=True, **layer_codes)
         
    return output_layer

def tile_scenario(
    base_project_dir, scenario_input_dir, mask, fire_draw_dir, preprocessing_dir,
    include_salvage=False
):
    fire_severity_dir = preprocessing_dir.joinpath(r"env_fire_variables")

    mgr = SharedTransitionRuleManager()
    mgr.start()
    rule_manager = mgr.TransitionRuleManager()

    with cleanup():
        bounding_box = base_project_dir.joinpath(r"layers\tiled\initial_age_moja.tiff")
        tiler = GdalTiler2D(
            BoundingBox(RasterLayer(str(bounding_box)), preprocessed=True),
            use_bounding_box_resolution=True)
            
        layers = [
            RasterLayer(str(mask), name="common_inventory_mask", tags=["reporting_classifier"]),
            RasterLayer(str(fire_severity_dir.joinpath("bui_1980_2021_summer_95th_percentile.tif")), name="bui"),
            RasterLayer(str(fire_severity_dir.joinpath("ffmc_1980_2021_summer_95th_percentile.tif")), name="ffmc"),
            RasterLayer(str(fire_severity_dir.joinpath("isi_1980_2021_summer_95th_percentile.tif")), name="isi"),
            RasterLayer(str(preprocessing_dir.joinpath(
                r"env_fire_variables\elevation\cdem-canada-dem.tif"
            )), name="elevation"),
            *(DisturbanceLayer(
                rule_manager,
                RasterLayer(str(fire_layer), nodata_value=0),
                year=int(fire_layer.stem.split("_")[1]),
                disturbance_type="Future wildfire - high severity"
            ) for fire_layer in fire_draw_dir.glob("burn*.tif") if int(fire_layer.stem.split("_")[1]) > 2023)
        ]
        
        if include_salvage:
            layers.extend([
                DisturbanceLayer(
                    rule_manager,
                    RasterLayer(str(fire_layer), nodata_value=0, name=f"{fire_layer.stem}_salvage"),
                    year=int(fire_layer.stem.split("_")[1]),
                    disturbance_type="Rehabilitation after fire"
                ) for fire_layer in fire_draw_dir.glob("burn*.tif") if int(fire_layer.stem.split("_")[1]) > 2023
            ])
            
        tiler.tile(layers, str(scenario_input_dir))
        
def load_transition_rules(input_db_path, transition_rules_path):
    with sqlite3.connect(str(input_db_path)) as gcbm_input_db:
        wildcard_classifier_values = {
            classifier_name: classifier_value_id
            for (classifier_name, classifier_value_id) in gcbm_input_db.execute(
                """
                SELECT c.name, cv.id AS classifier_value_id
                FROM classifier_value cv
                INNER JOIN classifier c
                    ON cv.classifier_id = c.id
                WHERE cv.value = '?'
                """)
        }
        
        rule_classifier_sql = \
            """
            SELECT c.name, cv.id AS classifier_value_id
            FROM classifier_value cv
            INNER JOIN classifier c
                ON cv.classifier_id = c.id
            WHERE {}
            """
        
        rule_classifier_where = "(c.name = '{}' AND cv.value = '{}')"
        
        for i, rule_data in enumerate(csv.reader(open(str(transition_rules_path)))):
            if i == 0:
                header = rule_data
                dist_type_col = header.index("disturbance_type")
                continue
            
            rule_where_items = [
                rule_classifier_where.format(n, v)
                for n, v in zip(header[1:dist_type_col], rule_data[1:dist_type_col])
            ]
            
            rule_classifier_values = {
                classifier_name: classifier_value_id
                for (classifier_name, classifier_value_id) in gcbm_input_db.execute(
                    rule_classifier_sql.format(" OR ".join(rule_where_items)))
            }
            
            rule_classifier_set = wildcard_classifier_values.copy()
            rule_classifier_set.update(rule_classifier_values)
            
            rule_name = rule_data[0]
            gcbm_input_db.execute(
                "INSERT INTO transition (transition_type_id, age, regen_delay, description) VALUES (?, ?, ?, ?)",
                (1, rule_data[header.index("age_after")], rule_data[header.index("regen_delay")], rule_name)
            )
            
            gcbm_input_db.execute(
                """
                INSERT INTO transition_rule (transition_id, disturbance_type_id)
                SELECT t.id, dt.id
                FROM transition t, disturbance_type dt
                WHERE t.description = ? AND dt.name = ?
                """, (rule_name, rule_data[dist_type_col]))
            
            gcbm_input_db.execute(
                f"""
                INSERT INTO transition_rule_classifier_value (transition_rule_id, classifier_value_id)
                SELECT tr.id, cv.id
                FROM classifier_value cv, transition_rule tr
                INNER JOIN transition t
                    ON tr.transition_id = t.id
                WHERE t.description = ?
                    AND cv.id IN ({','.join('?' * len(rule_classifier_set))})
                """, [rule_name] + list(rule_classifier_set.values()))
                
        gcbm_input_db.commit()

if __name__ == "__main__":
    log_path = Path().joinpath("prepare_future_runs.log")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[
        FileHandler(log_path, mode=("w")),
        StreamHandler()
    ])
    
    preprocessing_root = Path("00_preprocessing").absolute()
    transition_rules_path = preprocessing_root.joinpath(r"aspatial\regen_delay_transition_rules.csv")
    future_harvest_root = preprocessing_root.joinpath(r"dist_future_projections\harvest").absolute()
    fire_draw_root = preprocessing_root.joinpath(r"data_analysis\Future_Fire_Spatialization\OUTPUT").absolute()
    
    output_root = Path("15_seww_runs").absolute()
    output_root.mkdir(exist_ok=True)
    
    for province_root in list(Path().glob("??_??")):
        province = province_root.name.split("_")[1]

        province_output_root = output_root.joinpath(province)
        shutil.rmtree(province_output_root, ignore_errors=True)
        province_output_root.mkdir(parents=True, exist_ok=True)
        
        merged_inventories = list(filter(
            lambda p: p.is_dir and "merged_inv" in p.name,
            province_root.iterdir()))
        
        # Create a mask layer of the common pixels between all merged inventories
        # that were actually able to be simulated; this requires that the base
        # merged inventories were run through GCBM and the results downloaded.
        province_mask = create_common_mask(merged_inventories, province_output_root)

        for base_project_dir in merged_inventories:
            inventory = base_project_dir.name[6:]
            
            for scenario in ("BAU", "A"):
                for fire_draw_dir in fire_draw_root.rglob("draw*"):
                    fire_draw_title = "_".join((fire_draw_dir.parent.name, fire_draw_dir.name))
                    if "medium" not in fire_draw_title:
                        continue
                    logging.info(f"Processing {province}/{inventory}/{fire_draw_title}/{scenario}")
                    
                    scenario_dir = province_output_root.joinpath(inventory, fire_draw_title, scenario)
                    scenario_input_dir = scenario_dir.joinpath("input_database")
                    scenario_gcbm_config_dir = scenario_dir.joinpath("gcbm_project")
                    scenario_layers_dir = scenario_dir.joinpath(r"layers\tiled")

                    for subdir in (scenario_input_dir, scenario_gcbm_config_dir, scenario_layers_dir):
                        subdir.mkdir(parents=True, exist_ok=True)
                    
                    tile_scenario(
                        base_project_dir, scenario_layers_dir, province_mask,
                        fire_draw_dir, preprocessing_root, scenario == "A")
                    
                    scenario_input_db_path = scenario_input_dir.joinpath("gcbm_input.db")
                    shutil.copyfile(
                        base_project_dir.joinpath(r"input_database\gcbm_input.db"),
                        scenario_input_db_path)
                        
                    load_transition_rules(scenario_input_db_path, transition_rules_path)
                    load_fuel_bed_tables(scenario_input_db_path)
                    
                    tiled_layer_paths = [
                        str(base_project_dir.joinpath(r"layers\tiled")),
                        str(scenario_layers_dir)
                    ]
                    
                    future_harvest_layers_dir = future_harvest_root.joinpath(province_root.name, "merged")
                    if future_harvest_layers_dir.exists():
                        tiled_layer_paths.append(str(future_harvest_layers_dir))
                    
                    GCBMConfigurer(
                        tiled_layer_paths,
                        r"..\03_tools_archive\gcbm_config_templates\projected_runs",
                        str(scenario_input_db_path),
                        str(scenario_gcbm_config_dir),
                        1990, 2070,
                        [line.replace("\n", "") for line in open(r"00_walltowall_config\disturbance_order.txt")],
                        ["merged_index"]
                    ).configure()
