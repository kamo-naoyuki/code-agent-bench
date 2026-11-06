import re


def parse_record(text):
    pattern = re.compile(r'(?:"((?:[^"]|"")*)"|([^",]*))(,|$)')
    result = []
    position = 0
    while True:
        match = pattern.match(text, position)
        if match is None:
            raise ValueError("Malformed field")
        quoted, plain, separator = match.groups()
        result.append(quoted.replace('""', '"') if quoted is not None else plain)
        if not separator:
            return result
        position = match.end()


def join_plain(fields):
    return ",".join(fields)
