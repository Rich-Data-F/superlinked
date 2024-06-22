# Copyright 2024 Superlinked, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import logging
import traceback
import uuid
from collections.abc import Sequence
from typing import Any

import pandas as pd
from pandas.io.json._json import JsonReader
from pandas.io.parsers import TextFileReader
from superlinked.framework.dsl.source.data_loader_source import DataFormat, DataLoaderSource

from executor.app.configuration.app_config import AppConfig

logger = logging.getLogger(__name__)


class DataLoader:
    def __init__(self, app_config: AppConfig) -> None:
        self._data_loader_sources: set[DataLoaderSource] = set()
        self._app_config = app_config
        self._data_loader_tasks: set[asyncio.Task] = set()

    def register_data_loader_sources(self, data_loader_sources: Sequence[DataLoaderSource]) -> None:
        for source in data_loader_sources:
            if source in self._data_loader_sources:
                logger.warning("Detected duplicate data loader sources. Configuration: %s", source.config)
                continue
            self._data_loader_sources.add(source)

    def load(self) -> set[str]:
        total_sources = len(self._data_loader_sources)
        logger.info("Starting the loading process for %d data source(s).", total_sources)
        task_ids = set()
        for source in self._data_loader_sources:
            logger.info("Beginning data load for source with the following configuration: %s", source.config)
            task = asyncio.create_task(
                asyncio.to_thread(self.__read_and_put_data, source),
                name=str(uuid.uuid4()),
            )
            self._data_loader_tasks.add(task)
            task_ids.add(task.get_name())
        return task_ids

    def get_task_status_by_name(self, task_id: str) -> str | None:
        task = next((task for task in self._data_loader_tasks if task.get_name() == task_id), None)
        if task is None:
            return None

        if not task.done():
            return "Task is still running"

        if task.cancelled():
            return "Task was cancelled"

        if task.exception() is not None:
            if exc := task.exception():
                logger.error(traceback.format_exception(type(exc), exc, exc.__traceback__))
            return f"Task failed with exception: {task.exception()}. For traceback, check the logs."

        return "Task completed successfully"

    def __read_and_put_data(self, source: DataLoaderSource) -> None:
        data = self.__read_data(source.config.path, source.config.format, source.config.pandas_read_kwargs)
        if isinstance(data, pd.DataFrame):
            if logger.isEnabledFor(logging.DEBUG):
                data.info(memory_usage=True)
            logger.debug(
                "Data frame of size: %s has been loaded into memory. Beginning persistence process.", len(data)
            )
            source._source.put(data)  # noqa: SLF001 private-member-access
        elif isinstance(data, TextFileReader | JsonReader):
            for chunk in data:
                if logger.isEnabledFor(logging.DEBUG):
                    chunk.info(memory_usage=True)
                logger.debug(
                    "Chunk of size: %s has been loaded into memory. Beginning persistence process.", len(chunk)
                )
                source._source.put(chunk)  # noqa: SLF001 private-member-access
        else:
            error_message = (
                "The returned object from the Pandas read method was not of the "
                f"expected type. Actual type: {type(data)}"
            )
            raise TypeError(error_message)
        logger.info("Finished the data load for source with the following configuration: %s", source.config)

    def __read_data(
        self, path: str, data_format: DataFormat, pandas_read_kwargs: dict[str, Any] | None
    ) -> pd.DataFrame | TextFileReader | JsonReader:
        kwargs = pandas_read_kwargs or {}
        match data_format:
            case DataFormat.CSV:
                return pd.read_csv(path, **kwargs)
            case DataFormat.FWF:
                return pd.read_fwf(path, **kwargs)
            case DataFormat.XML:
                return pd.read_xml(path, **kwargs)
            case DataFormat.JSON:
                return pd.read_json(path, **kwargs)
            case DataFormat.PARQUET:
                return pd.read_parquet(path, **kwargs)
            case DataFormat.ORC:
                return pd.read_orc(path, **kwargs)
            case _:
                msg = "Unsupported data format: %s"
                raise ValueError(msg, data_format)
