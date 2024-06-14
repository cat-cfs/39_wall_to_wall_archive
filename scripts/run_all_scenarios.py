import json
import logging
import shutil
from logging import FileHandler
from logging import StreamHandler
from pathlib import Path
from types import SimpleNamespace
from gcbmwalltowall.application.walltowall import run

if __name__ == "__main__":
    compile_results_config_path = Path().absolute().parent.joinpath(
        r"03_tools_archive\compile_results\compileresults_trimmed.json"
    )

    log_path = Path().joinpath("walltowall_run_cluster.log")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[
        FileHandler(log_path, mode=("w")),
        StreamHandler()
    ])

    for gcbm_config_path in Path("15_seww_runs").rglob("*gcbm_config.cfg"):
        parts = str(gcbm_config_path).split("15_seww_runs")[1].split("\\")
        title = f"{parts[1]}_{parts[2].split('_')[2]}_{parts[3]}_{parts[4]}"

        if "low" in title:
            run(SimpleNamespace(
                host="cluster",
                config_path=None,
                project_path=gcbm_config_path.parent.parent,
                title=title,
                end_year=2070,
                compile_results_config=compile_results_config_path
            ))
