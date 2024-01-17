import json
import logging
import shutil
from logging import FileHandler
from logging import StreamHandler
from pathlib import Path
from types import SimpleNamespace
from gcbmwalltowall.application.walltowall import build
from gcbmwalltowall.application.walltowall import prepare
from gcbmwalltowall.application.walltowall import merge

if __name__ == "__main__":
    log_path = Path().joinpath("walltowall.log")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[
        FileHandler(log_path, mode=("w")),
        StreamHandler()
    ])

    for builder_config_path in Path().rglob("*_builder.json"):
        if "00" not in str(builder_config_path):
            project_name = json.load(open(builder_config_path))["project_name"]
            build(SimpleNamespace(config_path=builder_config_path, output_path=None))
            try:
                prepare(SimpleNamespace(
                    config_path=builder_config_path.parent.joinpath(f"{project_name}.json"),
                    output_path=builder_config_path.parent.parent
                ))
            except Exception as e:
                logging.error(f"Failed to prepare {project_name}: {e}")

    for province_root in Path().glob("??_??"):
        province = province_root.stem.split("_")[1]

        # Inventory 1: CASFRI age/CASFRI species + BGI age/NTEMS species. Not all provinces have
        # CASFRI inventory, so in that case, just use BGI by itself.
        inventory_1_output_path = province_root.joinpath(f"06_{province}_merged_inv_1_casfri_bgi")
        if not province_root.joinpath(rf"02_{province}_casfri_nbac_ntems\config").exists():
            shutil.rmtree(inventory_1_output_path, ignore_errors=True)
            shutil.copytree(
                province_root.joinpath(f"03_{province}_bgi_txomin_nbac_ntems"),
                inventory_1_output_path
            )
        else:
            merge(SimpleNamespace(
                config_path=province_root.joinpath(
                    rf"02_{province}_casfri_nbac_ntems\config\{province}_casfri_nbac_ntems.json"),
                project_paths=[
                    province_root.joinpath(f"02_{province}_casfri_nbac_ntems"),
                    province_root.joinpath(f"03_{province}_bgi_txomin_nbac_ntems"),
                ],
                output_path=province_root.joinpath(f"06_{province}_merged_inv_1_casfri_bgi"),
                include_index_layer=True
            ))
            
        # Inventory 2: NTEMS age/NTEMS species + KNN age/NTEMS species.
        merge(SimpleNamespace(
            config_path=province_root.joinpath(
                rf"04_{province}_2019age_txomin_nbac_ntems\config\{province}_2019age_txomin_nbac_ntems.json"),
            project_paths=[
                province_root.joinpath(f"04_{province}_2019age_txomin_nbac_ntems"),
                province_root.joinpath(f"05_{province}_knn_txomin_nbac_ntems"),
            ],
            output_path=province_root.joinpath(f"07_{province}_merged_inv_2_ntems_knn"),
            include_index_layer=True
        ))
