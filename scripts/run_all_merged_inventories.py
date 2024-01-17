import json
import logging
import shutil
from logging import FileHandler
from logging import StreamHandler
from pathlib import Path
from types import SimpleNamespace
from gcbmwalltowall.application.walltowall import run

if __name__ == "__main__":
    log_path = Path().joinpath("walltowall_run_cluster.log")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[
        FileHandler(log_path, mode=("w")),
        StreamHandler()
    ])

    for gcbm_config_path in Path().rglob(r"*\*_merged_inv_*\*\gcbm_config.cfg"):
        title = "_".join(str(gcbm_config_path).split("\\")[1].split("_")[1:5])
        run(SimpleNamespace(
            host="cluster",
            config_path=None,
            project_path=gcbm_config_path.parent.parent,
            title=title,
            end_year=2023
        ))
