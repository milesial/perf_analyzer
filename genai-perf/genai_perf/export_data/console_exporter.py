# Copyright 2024, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#  * Neither the name of NVIDIA CORPORATION nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


from typing import Dict

from genai_perf.export_data.exporter_config import ExporterConfig
from rich.console import Console
from rich.table import Table
from rich.text import Text


class ConsoleExporter:
    """
    A class to export the statistics and arg values to the console.
    """

    STAT_COLUMN_KEYS = ["avg", "min", "max", "p99", "p90", "p75"]
    TELEMETRY_GROUPS = {
        "Power": ["gpu_power_usage", "gpu_power_limit", "energy_consumption"],
        "Memory": ["gpu_memory_used", "total_gpu_memory"],
        "Utilization": ["gpu_utilization"],
    }

    def __init__(self, config: ExporterConfig):
        self._stats = config.stats
        self._telemetry_stats = config.telemetry_stats
        self._metrics = config.metrics
        self._args = config.args

    def _get_title(self):
        if self._args.endpoint_type == "embeddings":
            return "Embeddings Metrics"
        elif self._args.endpoint_type == "rankings":
            return "Rankings Metrics"
        else:
            return "LLM Metrics"

    def export(self) -> None:
        console = Console()
        title = self._get_title()

        if self._args.verbose:
            self._export_telemetry_metrics(console)
        self._export_llm_metrics(console, title)

    def _export_llm_metrics(self, console: Console, title: str) -> None:
        table = Table(title=title)
        table.add_column("Metric", justify="left")
        for stat in self.STAT_COLUMN_KEYS:
            table.add_column(stat, justify="right", style="green")

        self._construct_llm_table(table)
        console.print(table)

        # System metrics are printed after the table
        for metric in self._metrics.system_metrics:
            line = metric.name.replace("_", " ").capitalize()
            value = self._stats[metric.name]["avg"]
            line += f" ({metric.unit}): {value:.2f}"
            print(line)

    def _construct_llm_table(self, table: Table) -> None:
        for metric in self._metrics.request_metrics:
            if self._should_skip(metric.name):
                continue

            metric_str = metric.name.replace("_", " ").capitalize()
            metric_str += f" ({metric.unit})" if metric.unit != "tokens" else ""
            row_values = [metric_str]
            for stat in self.STAT_COLUMN_KEYS:
                value = self._stats[metric.name][stat]
                row_values.append(f"{value:,.2f}")

            table.add_row(*row_values)

    def _export_telemetry_metrics(self, console: Console) -> None:
        for group_name, metrics in self.TELEMETRY_GROUPS.items():
            table = Table(title=f"{group_name} Metrics")

            for metric_name in metrics:
                metric_data = self._telemetry_stats.get(metric_name, {})

                unit = metric_data.get("unit", "N/A")
                metric_name_display = self._capitalize_abbreviation(
                    metric_name.replace("_", " ")
                )
                table_title = f"{metric_name_display}{f' ({unit})' if unit else ''}"
                sub_table = Table(title=table_title)

                sub_table.add_column("GPU Index", justify="left")
                for stat in self.STAT_COLUMN_KEYS:
                    sub_table.add_column(stat, justify="right", style="green")

                self._construct_telemetry_table(sub_table, metric_data)
                table.add_row(sub_table)

            console.print(table)

    def _construct_telemetry_table(
        self, table: Table, metric_data: Dict[str, Dict[str, float]]
    ) -> None:
        gpu_indices = [key for key in metric_data.keys() if key != "unit"]

        for gpu_index in gpu_indices:
            row = [f"{gpu_index}"]
            for stat in self.STAT_COLUMN_KEYS:
                value = metric_data.get(gpu_index, {}).get(stat, "N/A")
                row.append(f"{value:.2f}" if isinstance(value, (int, float)) else "N/A")
            table.add_row(*row)

    def _capitalize_abbreviation(self, text: str) -> str:
        """
        Capitalizes abbreviations (e.g., GPU) while normalizing other text.
        """
        words = text.split()
        capitalized_words = [
            word.upper() if word.lower() in ["gpu"] else word.capitalize()
            for word in words
        ]
        return " ".join(capitalized_words)

    # (TMA-1976) Refactor this method as the csv exporter shares identical method.
    def _should_skip(self, metric_name: str) -> bool:
        if self._args.endpoint_type == "embeddings":
            return False  # skip nothing

        # TODO (TMA-1712): need to decide if we need this metric. Remove
        # from statistics display for now.
        # TODO (TMA-1678): output_token_throughput_per_request is treated
        # separately since the current code treats all throughput metrics to
        # be displayed outside of the statistics table.
        if metric_name == "output_token_throughput_per_request":
            return True

        # When non-streaming, skip ITL and TTFT
        streaming_metrics = [
            "inter_token_latency",
            "time_to_first_token",
        ]
        if not self._args.streaming and metric_name in streaming_metrics:
            return True
        return False
