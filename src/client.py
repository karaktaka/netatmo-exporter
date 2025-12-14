#!/usr/bin/env python3
# encoding=utf-8

import argparse
import json
import logging
import signal
from enum import Enum
from os import getenv
from pathlib import Path
from time import sleep
from typing import Dict, Optional

import requests
import yaml
from prometheus_client import Gauge, start_http_server

from helpers import configure_logging
from netatmo_api import (
    NetatmoAPI,
    NetatmoAPIError,
    NetatmoAuthError,
    NetatmoThrottlingError,
)

# Prometheus Metrics
STATION_REACHABLE = Gauge(
    "netatmo_station_reachable",
    "If the station is reachable",
    ["home", "station", "type", "city", "country", "timezone"],
)
STATION_LONGITUDE = Gauge("netatmo_station_longitude", "The Longitude of the Station", ["home", "station", "type"])
STATION_LATITUDE = Gauge("netatmo_station_latitude", "The Latitude of the Station", ["home", "station", "type"])
STATION_ALTITUDE = Gauge("netatmo_station_altitude", "The Altitude of the Station", ["home", "station", "type"])
STATION_WIFI_STATUS = Gauge("netatmo_station_wifi_status", "The current Wifi Status", ["home", "station", "type"])
STATION_CO2_CALIBRATING = Gauge(
    "netatmo_station_co2_calibrating", "The current CO2 Calibrating Status", ["home", "station", "type"]
)
TEMPERATURE = Gauge("netatmo_temperature", "The current Temperature", ["home", "station", "module", "type"])
MIN_TEMP = Gauge("netatmo_temperature_min", "The current Min Temperature", ["home", "station", "module", "type"])
MAX_TEMP = Gauge("netatmo_temperature_max", "The current Max Temperature", ["home", "station", "module", "type"])
TEMP_TREND = Gauge("netatmo_temperature_trend", "The current Temperature Trend", ["home", "station", "module", "type"])
HUMIDITY = Gauge("netatmo_humidity", "The current Humidity", ["home", "station", "module", "type"])
CO2 = Gauge("netatmo_co2", "The current CO2", ["home", "station", "module", "type"])
PRESSURE = Gauge("netatmo_pressure", "The current Pressure", ["home", "station", "module", "type"])
PRESSURE_TREND = Gauge("netatmo_pressure_trend", "The current Pressure Trend", ["home", "station", "module", "type"])
ABSOLUTEPRESSURE = Gauge(
    "netatmo_absolute_pressure", "The current Absolute Pressure", ["home", "station", "module", "type"]
)
NOISE = Gauge("netatmo_noise", "The current Noise", ["home", "station", "module", "type"])
RF_STATUS = Gauge("netatmo_rf_status", "The current RF Status", ["home", "station", "module", "type"])
BATTERY_VP = Gauge("netatmo_battery_vp", "The current Battery VP", ["home", "station", "module", "type"])
BATTERY_PERCENT = Gauge("netatmo_battery_percent", "The current Battery Percent", ["home", "station", "module", "type"])
WINDANGLE = Gauge("netatmo_wind_angle", "The current Wind Angle", ["home", "station", "module", "type"])
WINDSTRENGTH = Gauge("netatmo_wind_strength", "The current Wind Strength", ["home", "station", "module", "type"])
MAX_WIND_ANGLE = Gauge("netatmo_wind_max_angle", "The current Wind Max Angle", ["home", "station", "module", "type"])
MAX_WIND_STR = Gauge(
    "netatmo_wind_max_strength", "The current Wind Max Strength", ["home", "station", "module", "type"]
)
GUSTANGLE = Gauge("netatmo_gust_angle", "The current Gust Angle", ["home", "station", "module", "type"])
GUSTSTRENGTH = Gauge("netatmo_gust_strength", "The current Gust Strength", ["home", "station", "module", "type"])
RAIN = Gauge("netatmo_rain", "The current Rain", ["home", "station", "module", "type"])
SUM_RAIN_1 = Gauge("netatmo_rain_1h", "Rain over the last 1h", ["home", "station", "module", "type"])
SUM_RAIN_24 = Gauge("netatmo_rain_24h", "Rain over the last 24h", ["home", "station", "module", "type"])


class VerbosityLevel(Enum):
    NOTSET = 0
    WARNING = 1
    INFO = 2
    DEBUG = 3


class TrendState(Enum):
    UP = 1
    DOWN = -1
    STABLE = 0


def parse_config(_config_file: str = None) -> Dict:
    if _config_file is None:
        _config_file = Path(__file__).parent / "config.yaml"
    try:
        with open(_config_file, "r", encoding="utf-8") as _f:
            _config = yaml.safe_load(_f)
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as _error:
        if hasattr(_error, "problem_mark"):
            _mark = _error.problem_mark
            print("Error in configuration. Please check your configuration file for syntax errors.")
            print(f"Error position: ({_mark.line + 1}:{_mark.column + 1})")
        exit(1)
    else:
        return _config


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config-file", dest="config_file", type=str, nargs="?", default=None)
    parser.add_argument("-t", "--token-file", dest="token_file", type=str, nargs="?", default="data/token.json")
    parser.add_argument("-v", "--verbose", dest="verbosity", action="count", default=0)

    return parser.parse_args()


def safe_list_get(_input_list: list, _idx: int, _default=None) -> Optional[str | int | float]:
    try:
        return _input_list[_idx]
    except IndexError:
        return _default


def shutdown(_signal):
    global RUNNING
    RUNNING = False


def get_sensor_data(
    _sensor_data: dict, _home_name: str, _station_name: str, _module_name: str, _module_type: str
) -> None:
    if _sensor_data is not None:
        for _sensor, _value in _sensor_data.items():
            if _sensor in ["time_utc", "date_max_temp", "date_min_temp", "date_max_wind_str"]:
                continue
            if _sensor in ["temp_trend", "pressure_trend"]:
                globals()[_sensor.upper()].labels(_home_name, _station_name, _module_name, _module_type).set(
                    TrendState[_value.upper()].value
                )
                continue
            globals()[_sensor.upper()].labels(_home_name, _station_name, _module_name, _module_type).set(_value)


def main(_api: NetatmoAPI, _interval: int, _log: logging.Logger) -> None:
    while RUNNING:
        try:
            # Fetch weather station data
            _api.get_stations_data()

            _stations = _api.get_stations()

            for _station_id, _station in _stations.items():
                _log.debug(f"Station Data: {_station}")
                _home_name = _station.get("home_name", "Unknown")
                _station_name = _station.get("station_name", "Unknown")
                _station_module_name = _station.get("module_name", "Unknown")
                _station_module_type = _station.get("type", "Unknown")
                _station_place = _station.get("place", {})
                _station_country = _station_place.get("country", "Unknown")
                _station_timezone = _station_place.get("timezone", "Unknown")
                _station_city = _station_place.get("city", "Unknown")
                _station_long_lat = _station_place.get("location", [])

                _station_data = {
                    "altitude": _station_place.get("altitude"),
                    "longitude": safe_list_get(_station_long_lat, 0),
                    "latitude": safe_list_get(_station_long_lat, 1),
                }

                for _key, _value in _station_data.items():
                    globals()[f"STATION_{_key.upper()}"].labels(_home_name, _station_name, _station_module_type).set(
                        _value
                    )

                _station_sensor_data = _station.get("dashboard_data")

                if _station_sensor_data is None:
                    continue

                STATION_REACHABLE.labels(
                    _home_name, _station_name, _station_module_type, _station_city, _station_country, _station_timezone
                ).set(_station.get("reachable"))

                for _sensor in ["wifi_status", "co2_calibrating"]:
                    globals()[f"STATION_{_sensor.upper()}"].labels(_home_name, _station_name, _station_module_type).set(
                        _station.get(_sensor)
                    )

                get_sensor_data(
                    _station_sensor_data, _home_name, _station_name, _station_module_name, _station_module_type
                )

                for _module in _station.get("modules", []):
                    _log.debug(f"Module Data: {_module}")
                    _module_name = _module.get("module_name")
                    _module_type = _module.get("type")

                    _module_sensor_data = _module.get("dashboard_data")

                    if _module_sensor_data is None:
                        continue

                    for _sensor in ["rf_status", "battery_vp", "battery_percent"]:
                        globals()[f"{_sensor.upper()}"].labels(
                            _home_name, _station_name, _module_name, _module_type
                        ).set(_module.get(_sensor))

                    get_sensor_data(_module_sensor_data, _home_name, _station_name, _module_name, _module_type)
        except (json.decoder.JSONDecodeError, requests.exceptions.JSONDecodeError) as _error:
            _log.error(f"JSON Decode Error. Retry in {_interval} second(s)...")
            _log.debug(_error)
        except NetatmoThrottlingError as _error:
            _log.error(f"API Throttling. Retry in {_interval} second(s)...")
            _log.debug(_error)
        except NetatmoAPIError as _error:
            _log.error(f"API Error. Retry in {_interval} second(s)...")
            _log.debug(_error)
        except NetatmoAuthError as _error:
            _log.error(f"Auth Error: {_error}. Retry in {_interval} second(s)...")
        finally:
            sleep(_interval)


if __name__ == "__main__":
    RUNNING = True
    client_id = None
    client_secret = None
    refresh_token = None
    args = parse_args()
    config = parse_config(args.config_file)

    try:
        if getenv("TERM", None):
            # noinspection PyTypeChecker
            signal.signal(signal.SIGTERM, shutdown)
            # noinspection PyTypeChecker
            signal.signal(signal.SIGINT, shutdown)

        interval = int(config.get("interval", "300"))  # interval in seconds; default are 5 Minutes
        log_level = config.get("loglevel", "INFO")  # set loglevel by Name
        listen_port = config.get("listen_port", "9126")  # set loglevel for batching (influx)

        if "netatmo" in config:
            client_id = config.get("netatmo").get("client_id", None)
            client_secret = config.get("netatmo").get("client_secret", None)
            refresh_token = config.get("netatmo").get("refresh_token", None)

        # Environment Variables takes precedence over config if set
        # global
        interval = int(getenv("INTERVAL", interval))
        log_level = getenv("LOGLEVEL", VerbosityLevel(args.verbosity).name if args.verbosity > 0 else log_level)
        listen_port = getenv("LISTEN_PORT", listen_port)
        # netatmo
        client_id = getenv("NETATMO_CLIENT_ID", client_id)
        client_secret = getenv("NETATMO_CLIENT_SECRET", client_secret)
        # refresh_token needs to be persisted in the config, but can be set as env var for first run
        refresh_token = getenv("NETATMO_REFRESH_TOKEN", refresh_token)

        # Configure logging
        logger = logging.getLogger(__name__)
        log = configure_logging(logger, log_level)

        if client_id is None or client_secret is None or refresh_token is None:
            log.error("No credentials supplied. No Netatmo Account available.")
            exit(1)

        api = NetatmoAPI(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_file=args.token_file,
            log_level=log_level,
        )

        start_http_server(int(listen_port))
        log.info("Exporter ready...")
        main(api, interval, log)
    except KeyboardInterrupt:
        print("Received interrupt signal, shutting down...")
    except Exception as error:
        print(f"Fatal error: {error}")
        raise
    finally:
        RUNNING = False
