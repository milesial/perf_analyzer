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

import json
from io import StringIO
from pathlib import Path
from typing import Any, List, Union, cast

import numpy as np
import pytest
from genai_perf.metrics import LLMMetrics
from genai_perf.metrics.statistics import Statistics
from genai_perf.profile_data_parser import LLMProfileDataParser
from genai_perf.tokenizer import DEFAULT_TOKENIZER, get_tokenizer


def ns_to_sec(ns: int) -> Union[int, float]:
    """Convert from nanosecond to second."""
    return ns / 1e9


def check_statistics(s1: Statistics, s2: Statistics) -> None:
    s1_dict = s1.stats_dict
    s2_dict = s2.stats_dict
    for metric in s1_dict.keys():
        for stat_name, value in s1_dict[metric].items():
            if stat_name != "unit":
                assert s2_dict[metric][stat_name] == pytest.approx(value)


def check_llm_metrics(m1: LLMMetrics, m2: LLMMetrics) -> None:
    assert m1.request_latencies == m2.request_latencies
    assert m1.request_throughputs == pytest.approx(m2.request_throughputs)
    assert m1.time_to_first_tokens == m2.time_to_first_tokens
    assert m1.inter_token_latencies == m2.inter_token_latencies
    assert m1.output_token_throughputs_per_request == pytest.approx(
        m2.output_token_throughputs_per_request
    )
    assert m1.output_token_throughputs == pytest.approx(m2.output_token_throughputs)
    assert m1.output_sequence_lengths == m2.output_sequence_lengths
    assert m1.input_sequence_lengths == m2.input_sequence_lengths


class TestLLMProfileDataParser:
    @pytest.fixture
    def mock_read_write(self, monkeypatch: pytest.MonkeyPatch) -> List[str]:
        """
        This function will mock the open function for specific files:

        - For "triton_profile_export.json", it will read and return the
          contents of self.triton_profile_data
        - For "openai_profile_export.json", it will read and return the
          contents of self.openai_profile_data
        - For "profile_export.csv", it will capture all data written to
          the file, and return it as the return value of this function
        - For all other files, it will behave like the normal open function
        """

        written_data = []

        original_open = open

        def custom_open(filename, *args, **kwargs):
            def write(self: Any, content: str) -> int:
                written_data.append(content)
                return len(content)

            if filename == "triton_profile_export.json":
                tmp_file = StringIO(json.dumps(self.triton_profile_data))
                return tmp_file
            elif filename == "openai_profile_export.json":
                tmp_file = StringIO(json.dumps(self.openai_profile_data))
                return tmp_file
            elif filename == "openai_vlm_profile_export.json":
                tmp_file = StringIO(json.dumps(self.openai_vlm_profile_data))
                return tmp_file
            elif filename == "tensorrtllm_engine_profile_export.json":
                tmp_file = StringIO(json.dumps(self.tensorrtllm_engine_profile_data))
                return tmp_file
            elif filename == "empty_profile_export.json":
                tmp_file = StringIO(json.dumps(self.empty_profile_data))
                return tmp_file
            elif filename == "unfinished_responses_profile_export.json":
                tmp_file = StringIO(json.dumps(self.unfinished_responses_profile_data))
                return tmp_file
            elif filename == "profile_export.csv":
                tmp_file = StringIO()
                tmp_file.write = write.__get__(tmp_file)
                return tmp_file
            else:
                return original_open(filename, *args, **kwargs)

        monkeypatch.setattr("builtins.open", custom_open)

        return written_data

    def test_triton_llm_profile_data(self, mock_read_write: pytest.MonkeyPatch) -> None:
        """Collect LLM metrics from profile export data and check values.

        Metrics
        * time to first tokens
            - experiment 1: [3 - 1, 4 - 2] = [2, 2]
            - experiment 2: [7 - 5, 6 - 3] = [2, 3]
        * inter token latencies
            - experiment 1: [((8 - 1) - 2)/(3 - 1), ((11 - 2) - 2)/(6 - 1)]
                          : [2.5, 1.4]
                          : [2, 1]  # rounded
            - experiment 2: [((18 - 5) - 2)/(4 - 1), ((11 - 3) - 3)/(6 - 1)]
                          : [11/3, 1]
                          : [4, 1]  # rounded
        * output token throughputs per request
            - experiment 1: [3/(8 - 1), 6/(11 - 2)] = [3/7, 6/9]
            - experiment 2: [4/(18 - 5), 6/(11 - 3)] = [4/13, 6/8]
        * output token throughputs
            - experiment 1: [(3 + 6)/(11 - 1)] = [9/10]
            - experiment 2: [(4 + 6)/(18 - 3)] = [2/3]
        * output sequence lengths
            - experiment 1: [3, 6]
            - experiment 2: [4, 6]
        * input sequence lengths
            - experiment 1: [3, 4]
            - experiment 2: [3, 4]
        * request goodputs
            - experiment 1: [1 / (11 - 1)] = [1 / 10]
            - experiment 2: [0 / (18 - 3)] = [0]
        """
        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        test_goodput_constraints = {
            "time_to_first_token": 2.5e-6,  # ms
            "inter_token_latency": 2.5e-6,  # ms
            "output_token_throughput_per_request": 0.5e9,  # s
        }
        pd = LLMProfileDataParser(
            filename=Path("triton_profile_export.json"),
            tokenizer=tokenizer,
            goodput_constraints=test_goodput_constraints,
        )

        # experiment 1 metrics & statistics
        stat_obj = pd.get_statistics(infer_mode="concurrency", load_level="10")
        metrics = stat_obj.metrics
        stat = stat_obj.stats_dict

        assert isinstance(metrics, LLMMetrics)

        assert metrics.time_to_first_tokens == [2, 2]
        assert metrics.inter_token_latencies == [2, 1]
        ottpr = [3 / ns_to_sec(7), 6 / ns_to_sec(9)]
        assert metrics.output_token_throughputs_per_request == pytest.approx(ottpr)
        ott = [9 / ns_to_sec(10)]
        assert metrics.output_token_throughputs == pytest.approx(ott)
        assert metrics.output_sequence_lengths == [3, 6]
        assert metrics.input_sequence_lengths == [3, 4]
        gp = [1 / ns_to_sec(10)]
        assert metrics.request_goodputs == pytest.approx(gp)

        # Disable Pylance warnings for dynamically set attributes due to Statistics
        # not having strict attributes listed.
        assert stat["time_to_first_token"]["avg"] == 2  # type: ignore
        assert stat["inter_token_latency"]["avg"] == 1.5  # type: ignore
        assert stat["output_token_throughput_per_request"]["avg"] == pytest.approx(  # type: ignore
            np.mean(ottpr)
        )
        assert stat["output_sequence_length"]["avg"] == 4.5  # type: ignore
        assert stat["input_sequence_length"]["avg"] == 3.5  # type: ignore

        assert stat["time_to_first_token"]["p50"] == 2  # type: ignore
        assert stat["inter_token_latency"]["p50"] == 1.5  # type: ignore
        assert stat["output_token_throughput_per_request"]["p50"] == pytest.approx(  # type: ignore
            np.percentile(ottpr, 50)
        )
        assert stat["output_sequence_length"]["p50"] == 4.5  # type: ignore
        assert stat["input_sequence_length"]["p50"] == 3.5  # type: ignore

        assert stat["time_to_first_token"]["min"] == 2  # type: ignore
        assert stat["inter_token_latency"]["min"] == 1  # type: ignore
        min_ottpr = 3 / ns_to_sec(7)
        assert stat["output_token_throughput_per_request"]["min"] == pytest.approx(min_ottpr)  # type: ignore
        assert stat["output_sequence_length"]["min"] == 3  # type: ignore
        assert stat["input_sequence_length"]["min"] == 3  # type: ignore

        assert stat["time_to_first_token"]["max"] == 2  # type: ignore
        assert stat["inter_token_latency"]["max"] == 2  # type: ignore
        max_ottpr = 6 / ns_to_sec(9)
        assert stat["output_token_throughput_per_request"]["max"] == pytest.approx(max_ottpr)  # type: ignore
        assert stat["output_sequence_length"]["max"] == 6  # type: ignore
        assert stat["input_sequence_length"]["max"] == 4  # type: ignore

        assert stat["time_to_first_token"]["std"] == np.std([2, 2])  # type: ignore
        assert stat["inter_token_latency"]["std"] == np.std([2, 1])  # type: ignore
        assert stat["output_token_throughput_per_request"]["std"] == pytest.approx(  # type: ignore
            np.std(ottpr)
        )
        assert stat["output_sequence_length"]["std"] == np.std([3, 6])  # type: ignore
        assert stat["input_sequence_length"]["std"] == np.std([3, 4])  # type: ignore

        oott = 9 / ns_to_sec(10)
        assert stat["output_token_throughput"]["avg"] == pytest.approx(oott)  # type: ignore

        # experiment 2 statistics
        stat_obj = pd.get_statistics(infer_mode="request_rate", load_level="2.0")
        metrics = stat_obj.metrics
        stat = stat_obj.stats_dict
        assert isinstance(metrics, LLMMetrics)

        assert metrics.time_to_first_tokens == [2, 3]
        assert metrics.inter_token_latencies == [4, 1]
        ottpr = [4 / ns_to_sec(13), 6 / ns_to_sec(8)]
        assert metrics.output_token_throughputs_per_request == pytest.approx(ottpr)
        ott = [2 / ns_to_sec(3)]
        assert metrics.output_token_throughputs == pytest.approx(ott)
        assert metrics.output_sequence_lengths == [4, 6]
        assert metrics.input_sequence_lengths == [3, 4]
        gp = [0 / ns_to_sec(15)]
        assert metrics.request_goodputs == pytest.approx(gp)

        assert stat["time_to_first_token"]["avg"] == pytest.approx(2.5)  # type: ignore
        assert stat["inter_token_latency"]["avg"] == pytest.approx(2.5)  # type: ignore
        assert stat["output_token_throughput_per_request"]["avg"] == pytest.approx(  # type: ignore
            np.mean(ottpr)
        )
        assert stat["output_sequence_length"]["avg"] == 5  # type: ignore
        assert stat["input_sequence_length"]["avg"] == 3.5  # type: ignore

        assert stat["time_to_first_token"]["p50"] == pytest.approx(2.5)  # type: ignore
        assert stat["inter_token_latency"]["p50"] == pytest.approx(2.5)  # type: ignore
        assert stat["output_token_throughput_per_request"]["p50"] == pytest.approx(  # type: ignore
            np.percentile(ottpr, 50)
        )
        assert stat["output_sequence_length"]["p50"] == 5  # type: ignore
        assert stat["input_sequence_length"]["p50"] == 3.5  # type: ignore

        assert stat["time_to_first_token"]["min"] == pytest.approx(2)  # type: ignore
        assert stat["inter_token_latency"]["min"] == pytest.approx(1)  # type: ignore
        min_ottpr = 4 / ns_to_sec(13)
        assert stat["output_token_throughput_per_request"]["min"] == pytest.approx(min_ottpr)  # type: ignore
        assert stat["output_sequence_length"]["min"] == 4  # type: ignore
        assert stat["input_sequence_length"]["min"] == 3  # type: ignore

        assert stat["time_to_first_token"]["max"] == pytest.approx(3)  # type: ignore
        assert stat["inter_token_latency"]["max"] == pytest.approx(4)  # type: ignore
        max_ottpr = 6 / ns_to_sec(8)
        assert stat["output_token_throughput_per_request"]["max"] == pytest.approx(max_ottpr)  # type: ignore
        assert stat["output_sequence_length"]["max"] == 6  # type: ignore
        assert stat["input_sequence_length"]["max"] == 4  # type: ignore

        assert stat["time_to_first_token"]["std"] == np.std([2, 3]) * (1)  # type: ignore
        assert stat["inter_token_latency"]["std"] == np.std([4, 1]) * (1)  # type: ignore
        assert stat["output_token_throughput_per_request"]["std"] == pytest.approx(  # type: ignore
            np.std(ottpr)
        )
        assert stat["output_sequence_length"]["std"] == np.std([4, 6])  # type: ignore
        assert stat["input_sequence_length"]["std"] == np.std([3, 4])  # type: ignore

        oott = 2 / ns_to_sec(3)
        assert stat["output_token_throughput"]["avg"] == pytest.approx(oott)  # type: ignore

        # check non-existing profile data
        with pytest.raises(KeyError):
            pd.get_statistics(infer_mode="concurrency", load_level="30")

    def test_openai_llm_profile_data(self, mock_read_write: pytest.MonkeyPatch) -> None:
        """Collect LLM metrics from profile export data and check values.

        Metrics
        * time to first tokens
            - experiment 1: [5 - 1, 7 - 2] = [4, 5]
        * inter token latencies
            - experiment 1: [((12 - 1) - 4)/(3 - 1), ((15 - 2) - 5)/(6 - 1)]
                          : [3.5, 1.6]
                          : [4, 2]  # rounded
        * output token throughputs per request
            - experiment 1: [3/(12 - 1), 6/(15 - 2)] = [3/11, 6/13]
        * output token throughputs
            - experiment 1: [(3 + 6)/(15 - 1)] = [9/14]
        * output sequence lengths
            - experiment 1: [3, 6]
        * input sequence lengths
            - experiment 1: [3, 4]
        * request goodputs
            - experiment 1: [1 / (15 - 1)] = [1/14]
        """
        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        test_goodput_constraints = {
            "time_to_first_token": 5e-6,  # ms
            "inter_token_latency": 3.5e-6,  # ms
            "output_token_throughput_per_request": 0.4e9,  # s
        }
        pd = LLMProfileDataParser(
            filename=Path("openai_profile_export.json"),
            tokenizer=tokenizer,
            goodput_constraints=test_goodput_constraints,
        )

        # experiment 1 statistics
        stat_obj = pd.get_statistics(infer_mode="concurrency", load_level="10")
        metrics = stat_obj.metrics
        stat = stat_obj.stats_dict
        assert isinstance(metrics, LLMMetrics)

        assert metrics.time_to_first_tokens == [4, 5]
        assert metrics.inter_token_latencies == [4, 2]
        ottpr = [3 / ns_to_sec(11), 6 / ns_to_sec(13)]
        assert metrics.output_token_throughputs_per_request == pytest.approx(ottpr)
        ott = [9 / ns_to_sec(14)]
        assert metrics.output_token_throughputs == pytest.approx(ott)
        assert metrics.output_sequence_lengths == [3, 6]
        assert metrics.input_sequence_lengths == [3, 4]
        gp = [1 / ns_to_sec(14)]
        assert metrics.request_goodputs == pytest.approx(gp)

        assert stat["time_to_first_token"]["avg"] == pytest.approx(4.5)  # type: ignore
        assert stat["inter_token_latency"]["avg"] == pytest.approx(3)  # type: ignore
        assert stat["output_token_throughput_per_request"]["avg"] == pytest.approx(  # type: ignore
            np.mean(ottpr)
        )
        assert stat["output_sequence_length"]["avg"] == 4.5  # type: ignore
        assert stat["input_sequence_length"]["avg"] == 3.5  # type: ignore

        assert stat["time_to_first_token"]["p50"] == pytest.approx(4.5)  # type: ignore
        assert stat["inter_token_latency"]["p50"] == pytest.approx(3)  # type: ignore
        assert stat["output_token_throughput_per_request"]["p50"] == pytest.approx(  # type: ignore
            np.percentile(ottpr, 50)
        )
        assert stat["output_sequence_length"]["p50"] == 4.5  # type: ignore
        assert stat["input_sequence_length"]["p50"] == 3.5  # type: ignore

        assert stat["time_to_first_token"]["min"] == pytest.approx(4)  # type: ignore
        assert stat["inter_token_latency"]["min"] == pytest.approx(2)  # type: ignore
        min_ottpr = 3 / ns_to_sec(11)
        assert stat["output_token_throughput_per_request"]["min"] == pytest.approx(min_ottpr)  # type: ignore
        assert stat["output_sequence_length"]["min"] == 3  # type: ignore
        assert stat["input_sequence_length"]["min"] == 3  # type: ignore

        assert stat["time_to_first_token"]["max"] == pytest.approx(5)  # type: ignore
        assert stat["inter_token_latency"]["max"] == pytest.approx(4)  # type: ignore
        max_ottpr = 6 / ns_to_sec(13)
        assert stat["output_token_throughput_per_request"]["max"] == pytest.approx(max_ottpr)  # type: ignore
        assert stat["output_sequence_length"]["max"] == 6  # type: ignore
        assert stat["input_sequence_length"]["max"] == 4  # type: ignore

        assert stat["time_to_first_token"]["std"] == np.std([4, 5]) * (1)  # type: ignore
        assert stat["inter_token_latency"]["std"] == np.std([4, 2]) * (1)  # type: ignore
        assert stat["output_token_throughput_per_request"]["std"] == pytest.approx(  # type: ignore
            np.std(ottpr)
        )
        assert stat["output_sequence_length"]["std"] == np.std([3, 6])  # type: ignore
        assert stat["input_sequence_length"]["std"] == np.std([3, 4])  # type: ignore

        oott = 9 / ns_to_sec(14)
        assert stat["output_token_throughput"]["avg"] == pytest.approx(oott)  # type: ignore

        # check non-existing profile data
        with pytest.raises(KeyError):
            pd.get_statistics(infer_mode="concurrency", load_level="40")

    def test_openai_vlm_profile_data(self, mock_read_write: pytest.MonkeyPatch) -> None:
        """Collect LLM metrics from profile export data and check values.

        Metrics
        * time to first tokens
            - experiment 1: [5 - 1, 7 - 2] = [4, 5]
        * inter token latencies
            - experiment 1: [((12 - 1) - 4)/(3 - 1), ((15 - 2) - 5)/(6 - 1)]
                          : [3.5, 1.6]
                          : [4, 2]  # rounded
        * output token throughputs per request
            - experiment 1: [3/(12 - 1), 6/(15 - 2)] = [3/11, 6/13]
        * output token throughputs
            - experiment 1: [(3 + 6)/(15 - 1)] = [9/14]
        * output sequence lengths
            - experiment 1: [3, 6]
        * input sequence lengths
            - experiment 1: [3, 4]
         * request goodputs
            - experiment 1: [1 / (15 - 1)] = [1/14]
        """
        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        test_goodput_constraints = {
            "time_to_first_token": 5e-6,  # ms
            "inter_token_latency": 3.5e-6,  # ms
            "output_token_throughput_per_request": 0.4e9,  # s
        }
        pd = LLMProfileDataParser(
            filename=Path("openai_vlm_profile_export.json"),
            tokenizer=tokenizer,
            goodput_constraints=test_goodput_constraints,
        )

        # experiment 1 statistics
        stat_obj = pd.get_statistics(infer_mode="concurrency", load_level="10")
        metrics = stat_obj.metrics
        stat = stat_obj.stats_dict
        assert isinstance(metrics, LLMMetrics)

        assert metrics.time_to_first_tokens == [4, 5]
        assert metrics.inter_token_latencies == [4, 2]
        ottpr = [3 / ns_to_sec(11), 6 / ns_to_sec(13)]
        assert metrics.output_token_throughputs_per_request == pytest.approx(ottpr)
        ott = [9 / ns_to_sec(14)]
        assert metrics.output_token_throughputs == pytest.approx(ott)
        assert metrics.output_sequence_lengths == [3, 6]
        assert metrics.input_sequence_lengths == [3, 4]
        gp = [1 / ns_to_sec(14)]
        assert metrics.request_goodputs == pytest.approx(gp)

        assert stat["time_to_first_token"]["avg"] == pytest.approx(4.5)  # type: ignore
        assert stat["inter_token_latency"]["avg"] == pytest.approx(3)  # type: ignore
        assert stat["output_token_throughput_per_request"]["avg"] == pytest.approx(  # type: ignore
            np.mean(ottpr)
        )
        assert stat["output_sequence_length"]["avg"] == 4.5  # type: ignore
        assert stat["input_sequence_length"]["avg"] == 3.5  # type: ignore

        assert stat["time_to_first_token"]["p50"] == pytest.approx(4.5)  # type: ignore
        assert stat["inter_token_latency"]["p50"] == pytest.approx(3)  # type: ignore
        assert stat["output_token_throughput_per_request"]["p50"] == pytest.approx(  # type: ignore
            np.percentile(ottpr, 50)
        )
        assert stat["output_sequence_length"]["p50"] == 4.5  # type: ignore
        assert stat["input_sequence_length"]["p50"] == 3.5  # type: ignore

        assert stat["time_to_first_token"]["min"] == pytest.approx(4)  # type: ignore
        assert stat["inter_token_latency"]["min"] == pytest.approx(2)  # type: ignore
        min_ottpr = 3 / ns_to_sec(11)
        assert stat["output_token_throughput_per_request"]["min"] == pytest.approx(min_ottpr)  # type: ignore
        assert stat["output_sequence_length"]["min"] == 3  # type: ignore
        assert stat["input_sequence_length"]["min"] == 3  # type: ignore

        assert stat["time_to_first_token"]["max"] == pytest.approx(5)  # type: ignore
        assert stat["inter_token_latency"]["max"] == pytest.approx(4)  # type: ignore
        max_ottpr = 6 / ns_to_sec(13)
        assert stat["output_token_throughput_per_request"]["max"] == pytest.approx(max_ottpr)  # type: ignore
        assert stat["output_sequence_length"]["max"] == 6  # type: ignore
        assert stat["input_sequence_length"]["max"] == 4  # type: ignore

        assert stat["time_to_first_token"]["std"] == np.std([4, 5]) * (1)  # type: ignore
        assert stat["inter_token_latency"]["std"] == np.std([4, 2]) * (1)  # type: ignore
        assert stat["output_token_throughput_per_request"]["std"] == pytest.approx(  # type: ignore
            np.std(ottpr)
        )
        assert stat["output_sequence_length"]["std"] == np.std([3, 6])  # type: ignore
        assert stat["input_sequence_length"]["std"] == np.std([3, 4])  # type: ignore

        oott = 9 / ns_to_sec(14)
        assert stat["output_token_throughput"]["avg"] == pytest.approx(oott)  # type: ignore

        # check non-existing profile data
        with pytest.raises(KeyError):
            pd.get_statistics(infer_mode="concurrency", load_level="40")

    @pytest.mark.parametrize(
        "infer_mode, load_level, expected_metrics",
        [
            (
                "concurrency",
                "10",
                {
                    "request_latencies": [7, 9],
                    "request_throughputs": [1 / ns_to_sec(5)],
                    "time_to_first_tokens": [2, 2],
                    "inter_token_latencies": [2, 4],
                    "output_token_throughputs_per_request": [
                        3 / ns_to_sec(7),
                        1 / ns_to_sec(3),
                    ],
                    "output_token_throughputs": [3 / ns_to_sec(5)],
                    "output_sequence_lengths": [3, 3],
                    "input_sequence_lengths": [3, 4],
                },
            ),
            (
                "request_rate",
                "2.0",
                {
                    "request_latencies": [13, 8],
                    "request_throughputs": [2 / ns_to_sec(15)],
                    "time_to_first_tokens": [2, 3],
                    "inter_token_latencies": [4, 2],
                    "output_token_throughputs_per_request": [
                        4 / ns_to_sec(13),
                        3 / ns_to_sec(8),
                    ],
                    "output_token_throughputs": [7 / ns_to_sec(15)],
                    "output_sequence_lengths": [4, 3],
                    "input_sequence_lengths": [3, 4],
                },
            ),
        ],
    )
    def test_tensorrtllm_engine_llm_profile_data(
        self,
        mock_read_write: pytest.MonkeyPatch,
        infer_mode,
        load_level,
        expected_metrics,
    ) -> None:
        """Collect LLM metrics from profile export data and check values.

        Metrics
        * request_latencies
            - experiment 1: [8 - 1, 11 - 2] = [7, 9]
            - experiment 2: [18 - 5, 11 -3] = [13, 8]
        * request_throughputs
            - experiment 1: [2/(11 - 1)] = [1/5]
            - experiment 2: [2/(18 - 3)] = [2/15]
        * time to first tokens
            - experiment 1: [3 - 1, 4 - 2] = [2, 2]
            - experiment 2: [7 - 5, 6 - 3] = [2, 3]
        * inter token latencies
            - experiment 1: [((8 - 1) - 2)/(3 - 1), ((11 - 2) - 2)/(3 - 1)]
                          : [2.5, 3.5]
                          : [2, 4]  # rounded
            - experiment 2: [((18 - 5) - 2)/(4 - 1), ((11 - 3) - 3)/(3 - 1)]
                          : [11/3, 2.5]
                          : [4, 2]  # rounded
        * output token throughputs per request
            - experiment 1: [3/(8 - 1), 3/(11 - 2)] = [3/7, 1/3]
            - experiment 2: [4/(18 - 5), 3/(11 - 3)] = [4/13, 3/8]
        * output token throughputs
            - experiment 1: [(3 + 3)/(11 - 1)] = [3/5]
            - experiment 2: [(4 + 3)/(18 - 3)] = [7/15]
        * output sequence lengths
            - experiment 1: [3, 3]
            - experiment 2: [4, 3]
        * input sequence lengths
            - experiment 1: [3, 4]
            - experiment 2: [3, 4]
        """
        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        pd = LLMProfileDataParser(
            filename=Path("tensorrtllm_engine_profile_export.json"),
            tokenizer=tokenizer,
        )

        statistics = pd.get_statistics(infer_mode=infer_mode, load_level=load_level)
        metrics = cast(LLMMetrics, statistics.metrics)

        expected_metrics = LLMMetrics(**expected_metrics)
        expected_statistics = Statistics(expected_metrics)

        check_llm_metrics(metrics, expected_metrics)
        check_statistics(statistics, expected_statistics)

        # check non-existing profile data
        with pytest.raises(KeyError):
            pd.get_statistics(infer_mode="concurrency", load_level="30")

    def test_merged_sse_response(self, mock_read_write: pytest.MonkeyPatch) -> None:
        """Test merging the multiple sse response."""
        res_timestamps = [0, 1, 2, 3]
        res_outputs = [
            {
                "response": 'data: {"choices":[{"delta":{"content":"aaa"}}],"object":"chat.completion.chunk"}\n\n'
            },
            {
                "response": (
                    'data: {"choices":[{"delta":{"content":"abc"}}],"object":"chat.completion.chunk"}\n\n'
                    'data: {"choices":[{"delta":{"content":"1234"}}],"object":"chat.completion.chunk"}\n\n'
                    'data: {"choices":[{"delta":{"content":"helloworld"}}],"object":"chat.completion.chunk"}\n\n'
                )
            },
            {"response": "data: [DONE]\n\n"},
        ]
        expected_response = '{"choices": [{"delta": {"content": "abc1234helloworld"}}], "object": "chat.completion.chunk"}'

        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        pd = LLMProfileDataParser(
            filename=Path("openai_profile_export.json"),
            tokenizer=tokenizer,
        )

        pd._preprocess_response(res_timestamps, res_outputs)
        assert res_outputs[1]["response"] == expected_response

    def test_openai_output_token_counts(
        self, mock_read_write: pytest.MonkeyPatch
    ) -> None:
        output_texts = [
            "Ad",
            "idas",
            " Orig",
            "inals",
            " are",
            " now",
            " available",
            " in",
            " more",
            " than",
        ]
        res_outputs = []
        for text in output_texts:
            response = f'data: {{"choices":[{{"delta":{{"content":"{text}"}}}}],"object":"chat.completion.chunk"}}\n\n'
            res_outputs.append({"response": response})

        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        pd = LLMProfileDataParser(
            filename=Path("openai_profile_export.json"),
            tokenizer=tokenizer,
        )

        output_token_counts, total_output_token = pd._get_output_token_counts(
            res_outputs
        )
        assert output_token_counts == [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]  # total 10
        assert total_output_token == 9
        assert total_output_token != sum(output_token_counts)

    def test_triton_output_token_counts(
        self, mock_read_write: pytest.MonkeyPatch
    ) -> None:
        output_texts = [
            "Ad",
            "idas",
            " Orig",
            "inals",
            " are",
            " now",
            " available",
            " in",
            " more",
            " than",
        ]
        res_outputs = []
        for text in output_texts:
            res_outputs.append({"text_output": text})

        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        pd = LLMProfileDataParser(
            filename=Path("triton_profile_export.json"),
            tokenizer=tokenizer,
        )

        output_token_counts, total_output_token = pd._get_output_token_counts(
            res_outputs
        )
        assert output_token_counts == [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]  # total 10
        assert total_output_token == 9
        assert total_output_token != sum(output_token_counts)

    def test_empty_response(self, mock_read_write: pytest.MonkeyPatch) -> None:
        """Check if it handles all empty responses."""
        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)

        # Should not throw error
        _ = LLMProfileDataParser(
            filename=Path("empty_profile_export.json"),
            tokenizer=tokenizer,
        )

    def test_unfinished_responses(self, mock_read_write: pytest.MonkeyPatch) -> None:
        """Check if it handles unfinished responses."""
        res_timestamps = [0, 1, 2]
        res_outputs = [
            {
                "response": 'data: {"id":"8ae835f2ecbb67f3-SJC","object":"chat.completion.chunk","created":1722875835,"choices":[{"index":0,"text"'
            },
            {
                "response": ':" writing","logprobs":null,"finish_reason":null,"seed":null,"delta":{"token_id":4477,"role":"assistant","content":" writing","tool_calls":null}}],"model":"meta-llama/Llama-3-8b-chat-hf","usage":null}'
            },
            {"response": "data: [DONE]\n\n"},
        ]
        expected_response = 'data: {"id":"8ae835f2ecbb67f3-SJC","object":"chat.completion.chunk","created":1722875835,"choices":[{"index":0,"text":" writing","logprobs":null,"finish_reason":null,"seed":null,"delta":{"token_id":4477,"role":"assistant","content":" writing","tool_calls":null}}],"model":"meta-llama/Llama-3-8b-chat-hf","usage":null}'

        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        pd = LLMProfileDataParser(
            filename=Path("openai_profile_export.json"),
            tokenizer=tokenizer,
        )

        pd._preprocess_response(res_timestamps, res_outputs)
        assert res_outputs[0]["response"] == expected_response

    def test_non_sse_response(self, mock_read_write: pytest.MonkeyPatch) -> None:
        """Check if it handles single responses."""
        res_timestamps = [
            0,
        ]
        res_outputs = [
            {
                "response": '{"id":"1","object":"chat.completion","created":2,"model":"gpt2","choices":[{"index":0,"message":{"role":"assistant","content":"A friend of mine, who is also a cook, writes a blog.","tool_calls":[]},"logprobs":null,"finish_reason":"length","stop_reason":null}],"usage":{"prompt_tokens":47,"total_tokens":1024,"completion_tokens":977}}'
            },
        ]
        expected_response = '{"id":"1","object":"chat.completion","created":2,"model":"gpt2","choices":[{"index":0,"message":{"role":"assistant","content":"A friend of mine, who is also a cook, writes a blog.","tool_calls":[]},"logprobs":null,"finish_reason":"length","stop_reason":null}],"usage":{"prompt_tokens":47,"total_tokens":1024,"completion_tokens":977}}'

        tokenizer = get_tokenizer(DEFAULT_TOKENIZER)
        pd = LLMProfileDataParser(
            filename=Path("openai_profile_export.json"),
            tokenizer=tokenizer,
        )

        pd._preprocess_response(res_timestamps, res_outputs)
        assert res_outputs[0]["response"] == expected_response

    empty_profile_data = {
        "service_kind": "openai",
        "endpoint": "v1/chat/completions",
        "experiments": [
            {
                "experiment": {
                    "mode": "concurrency",
                    "value": 10,
                },
                "requests": [
                    {
                        "timestamp": 1,
                        "request_inputs": {
                            "payload": '{"messages":[{"role":"user","content":"This is test"}],"model":"llama-2-7b","stream":true}',
                        },
                        "response_timestamps": [3, 5, 8],
                        "response_outputs": [
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"content":""},"finish_reason":null}]}\n\n'
                            },
                            {"response": "data: [DONE]\n\n"},
                        ],
                    },
                ],
            },
        ],
    }

    openai_profile_data = {
        "service_kind": "openai",
        "endpoint": "v1/chat/completions",
        "experiments": [
            {
                "experiment": {
                    "mode": "concurrency",
                    "value": 10,
                },
                "requests": [
                    {
                        "timestamp": 1,
                        "request_inputs": {
                            "payload": '{"messages":[{"role":"user","content":"This is test"}],"model":"llama-2-7b","stream":true}',
                        },
                        # the first, and the last two responses will be ignored because they have no "content"
                        "response_timestamps": [3, 5, 8, 12, 13, 14],
                        "response_outputs": [
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"content":"I"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"content":" like"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"content":" dogs"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{},"finish_reason":null}]}\n\n'
                            },
                            {"response": "data: [DONE]\n\n"},
                        ],
                    },
                    {
                        "timestamp": 2,
                        "request_inputs": {
                            "payload": '{"messages":[{"role":"user","content":"This is test too"}],"model":"llama-2-7b","stream":true}',
                        },
                        # the first, and the last two responses will be ignored because they have no "content"
                        "response_timestamps": [4, 7, 11, 15, 18, 19],
                        "response_outputs": [
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"content":"I"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"content":"don\'t"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{"content":"cook food"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"llama-2-7b","choices":[{"index":0,"delta":{},"finish_reason":null}]}\n\n'
                            },
                            {"response": "data: [DONE]\n\n"},
                        ],
                    },
                ],
            },
        ],
    }

    openai_vlm_profile_data = {
        "service_kind": "openai",
        "endpoint": "v1/chat/completions",
        "experiments": [
            {
                "experiment": {
                    "mode": "concurrency",
                    "value": 10,
                },
                "requests": [
                    {
                        "timestamp": 1,
                        "request_inputs": {
                            "payload": '{"messages":[{"role":"user","content":[{"type":"text","text":"This is test"},{"type":"image_url","image_url":{"url":"data:image/png;base64,abcdef"}}]}],"model":"llava-1.6","stream":true}',
                        },
                        # the first, and the last two responses will be ignored because they have no "content"
                        "response_timestamps": [3, 5, 8, 12, 13, 14],
                        "response_outputs": [
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"I"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" like"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" dogs"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":null}]}\n\n'
                            },
                            {"response": "data: [DONE]\n\n"},
                        ],
                    },
                    {
                        "timestamp": 2,
                        "request_inputs": {
                            "payload": '{"messages":[{"role":"user","content":[{"type":"text","text":"This is test too"},{"type":"image_url","image_url":{"url":"data:image/png;base64,abcdef"}}]}],"model":"llava-1.6","stream":true}',
                        },
                        # the first, and the last two responses will be ignored because they have no "content"
                        "response_timestamps": [4, 7, 11, 15, 18, 19],
                        "response_outputs": [
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"I"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"don\'t"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"cook food"},"finish_reason":null}]}\n\n'
                            },
                            {
                                "response": 'data: {"id":"abc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":null}]}\n\n'
                            },
                            {"response": "data: [DONE]\n\n"},
                        ],
                    },
                ],
            },
        ],
    }

    triton_profile_data = {
        "service_kind": "triton",
        "endpoint": "",
        "experiments": [
            {
                "experiment": {
                    "mode": "concurrency",
                    "value": 10,
                },
                "requests": [
                    {
                        "timestamp": 1,
                        "request_inputs": {"text_input": "This is test"},
                        "response_timestamps": [3, 5, 8],
                        "response_outputs": [
                            {"text_output": "I"},
                            {"text_output": " like"},
                            {"text_output": " dogs"},
                        ],
                    },
                    {
                        "timestamp": 2,
                        "request_inputs": {"text_input": "This is test too"},
                        "response_timestamps": [4, 7, 11],
                        "response_outputs": [
                            {"text_output": "I"},
                            {"text_output": " don't"},
                            {"text_output": " cook food"},
                        ],
                    },
                ],
            },
            {
                "experiment": {
                    "mode": "request_rate",
                    "value": 2.0,
                },
                "requests": [
                    {
                        "timestamp": 5,
                        "request_inputs": {"text_input": "This is test"},
                        "response_timestamps": [7, 8, 13, 18],
                        "response_outputs": [
                            {"text_output": "cat"},
                            {"text_output": " is"},
                            {"text_output": " cool"},
                            {"text_output": " too"},
                        ],
                    },
                    {
                        "timestamp": 3,
                        "request_inputs": {"text_input": "This is test too"},
                        "response_timestamps": [6, 8, 11],
                        "response_outputs": [
                            {"text_output": "it's"},
                            {"text_output": " very"},
                            {"text_output": " simple work"},
                        ],
                    },
                ],
            },
        ],
    }

    tensorrtllm_engine_profile_data = {
        "service_kind": "triton_c_api",
        "endpoint": "",
        "experiments": [
            {
                "experiment": {
                    "mode": "concurrency",
                    "value": 10,
                },
                "requests": [
                    {
                        "timestamp": 1,
                        "request_inputs": {
                            "streaming": True,
                            "request_output_len": 3,
                            "min_length": 3,
                            "input_lengths": 3,
                            "input_ids": [
                                111,
                                222,
                                333,
                            ],
                        },
                        "response_timestamps": [3, 5, 8],
                        "response_outputs": [
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 123,
                            },
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 456,
                            },
                            {
                                "output_ids": 789,
                            },
                        ],
                    },
                    {
                        "timestamp": 2,
                        "request_inputs": {
                            "streaming": True,
                            "request_output_len": 3,
                            "min_length": 3,
                            "input_lengths": 4,
                            "input_ids": [
                                111,
                                222,
                                333,
                                444,
                            ],
                        },
                        "response_timestamps": [4, 7, 11],
                        "response_outputs": [
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 123,
                            },
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 456,
                            },
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 789,
                            },
                        ],
                    },
                ],
            },
            {
                "experiment": {
                    "mode": "request_rate",
                    "value": 2.0,
                },
                "requests": [
                    {
                        "timestamp": 5,
                        "request_inputs": {
                            "streaming": True,
                            "request_output_len": 4,
                            "min_length": 4,
                            "input_lengths": 3,
                            "input_ids": [
                                111,
                                222,
                                333,
                            ],
                        },
                        "response_timestamps": [7, 8, 13, 18],
                        "response_outputs": [
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 123,
                            },
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 456,
                            },
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 789,
                            },
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 1011,
                            },
                        ],
                    },
                    {
                        "timestamp": 3,
                        "request_inputs": {
                            "streaming": True,
                            "request_output_len": 3,
                            "min_length": 3,
                            "input_lengths": 4,
                            "input_ids": [
                                111,
                                222,
                                333,
                                444,
                            ],
                        },
                        "response_timestamps": [6, 8, 11],
                        "response_outputs": [
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 123,
                            },
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 456,
                            },
                            {
                                "output_log_probs": [0, 0],
                                "output_ids": 789,
                            },
                        ],
                    },
                ],
            },
        ],
    }
