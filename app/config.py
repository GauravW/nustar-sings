"""
Configuration loader for the NuSTAR SINGS review interface.
Author: Kush Rao

Note:
- Reads the shared YAML selected explicitly, through NUSINGS_CONFIG, or from local_config.yaml.
- Resolves triggered, blind-candidate, and observation product locations.
- Requires two distinct existing source databases and existing product directories.
- Reads GRB_SECRET_KEY for signed sessions; no separate interface database is required.
"""
import os
from pathlib import Path
import yaml


def load_settings(config_path=None, require_secret=False):
    config_path = config_path or os.environ.get('NUSINGS_CONFIG') or Path(__file__).resolve().parents[1] / 'local_config.yaml'
    if not config_path:
        raise ValueError('Set NUSINGS_CONFIG or pass --config with the shared YAML path.')
    config_path = Path(config_path).expanduser().resolve()
    try:
        with config_path.open() as stream:
            config = yaml.safe_load(stream)
    except yaml.YAMLError as error:
        raise ValueError('Invalid YAML in the shared configuration.') from error
    if not isinstance(config, dict):
        raise ValueError('The configuration must contain a YAML mapping.')

    def path_value(section, key, base=None):
        section_values = config.get(section)
        if not isinstance(section_values, dict):
            raise ValueError(f'Missing configuration section: {section}')
        value = section_values.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Missing configuration value: {section}.{key}')
        path = Path(value).expanduser()
        return (path if path.is_absolute() else (base or config_path.parent) / path).resolve()

    blind_root = path_value('bs_paths', 'bs_products_dir')
    settings = {
        'TS_SOURCE_DATABASE': path_value('sings-paths', 'ts-queue-db'),
        'BS_SOURCE_DATABASE': path_value('bs_paths', 'bs_db'),
        'TS_PRODUCT_ROOT': path_value('sings-paths', 'ts-products-dir'),
        'BS_CANDIDATE_ROOT': path_value('bs_paths', 'cand_dir', blind_root),
        'OBS_PRODUCT_ROOT': path_value('bs_paths', 'obs_dir', blind_root),
    }
    ts, bs = settings['TS_SOURCE_DATABASE'], settings['BS_SOURCE_DATABASE']
    if ts == bs or (ts.exists() and bs.exists() and ts.samefile(bs)):
        raise ValueError('Triggered and blind databases must be different files.')
    for key in ('TS_SOURCE_DATABASE', 'BS_SOURCE_DATABASE'):
        if not settings[key].is_file():
            raise ValueError(f'{key} does not exist: {settings[key]}')
    for key in ('TS_PRODUCT_ROOT', 'BS_CANDIDATE_ROOT', 'OBS_PRODUCT_ROOT'):
        if not settings[key].is_dir():
            raise ValueError(f'{key} is not a directory: {settings[key]}')
    secret = os.environ.get('GRB_SECRET_KEY', '')
    if require_secret and not secret.strip():
        raise ValueError('Set GRB_SECRET_KEY to a stable private session secret.')
    settings['SECRET_KEY'] = secret
    return settings
