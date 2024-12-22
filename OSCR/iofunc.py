import os
import shutil
from time import time
from typing import Iterable


def format_timestamp(timestamp: str) -> str:
    '''
    Formats timestamp. '24:01:13:04:37:45.7' becomes '24-01-13_04:37:45'
    '''
    return timestamp.replace(':', '-', 2).replace(':', '_', 1).split('.')[0]


def extract_bytes(source_path: str, target_path: str, start_pos: int, end_pos: int):
    """
    Extracts combat from file at `source_path` by copying bytes from `start_pos` (including) up to
    `end_pos` (not including) to a new file at `target_path`

    Parameters:
    - :param source_path: path to source file, must be absolute
    - :param source_path: path to target_file, must be absolute, will overwrite if it already exists
    - :param start_pos: first byte from source file to copy
    - :param end_pos: copies data until this byte, not including it
    """
    if not os.path.isabs(source_path):
        raise AttributeError(f'source_path is not absolute: {source_path}')
    if not os.path.isabs(target_path):
        raise AttributeError(f'target_path is not absolute: {target_path}')
    with open(source_path, 'rb') as source_file:
        source_file.seek(start_pos)
        extracted_bytes = source_file.read(end_pos - start_pos)
    with open(target_path, 'wb') as target_file:
        target_file.write(extracted_bytes)


def compose_logfile(
            source_path: str, target_path: str, intervals: Iterable[tuple[int, int]],
            templog_folder_path: str):
    """
    Grabs bytes in given `intervals` from `source_path` and writes them to `target_path`.

    Parameters:
    - :param source_path: path to source file, must be absolute
    - :param target_path: path to target file, must be absolute, will overwrite if it already exists
    - :param intervals: iterable with start and end position pairs (half-open interval)
    - :param templog_folder_path: path to folder used for temporary logfiles
    """
    tempfile_path = f'{templog_folder_path}\\{int(time())}'
    with open(source_path, 'rb') as source_file, open(tempfile_path, 'wb') as temp_file:
        for start_pos, end_pos in intervals:
            source_file.seek(start_pos)
            temp_file.write(source_file.read(end_pos - start_pos))
    shutil.copyfile(tempfile_path, target_path)


def repair_logfile(path: str, templog_folder_path: str):
    """
    Replace bugged combatlog lines

    Parameters:
    - :param path: logfile to repair
    """
    patches = ((b'Rehona, Sister of the Qowat Milat', b'Rehona - Sister of the Qowat Milat'),)
    tempfile_path = f'{templog_folder_path}\\{int(time())}'
    with open(path, 'rb') as log_file, open(tempfile_path, 'wb') as temp_file:
        for line in log_file:
            if line.strip() == b'':
                continue
            for broken_string, fixed_string in patches:
                if broken_string in line:
                    temp_file.write(line.replace(broken_string, fixed_string))
                else:
                    temp_file.write(line)
    shutil.copyfile(tempfile_path, path)


def reset_temp_folder(path: str):
    '''
    Deletes and re-creates folder housing temporary log files.
    '''
    if os.path.exists(path):
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            raise FileExistsError(f'Expected path to folder, got "{path}"')
    os.mkdir(path)
