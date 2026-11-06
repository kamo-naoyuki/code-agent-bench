def parse_record(text):
    fields, field = [], []
    state = "start"
    for char in text:
        if state == "quoted":
            if char == '"':
                state = "closed"
            else:
                field.append(char)
        elif state == "closed":
            if char == '"':
                field.append('"')
                state = "quoted"
            elif char == ",":
                fields.append("".join(field))
                field, state = [], "start"
            else:
                raise ValueError("Unexpected character after quote")
        elif char == ",":
            fields.append("".join(field))
            field, state = [], "start"
        elif char == '"':
            if state != "start":
                raise ValueError("Quote inside unquoted field")
            state = "quoted"
        else:
            field.append(char)
            state = "plain"
    if state == "quoted":
        raise ValueError("Unclosed quote")
    return fields + ["".join(field)]


def join_plain(fields):
    return ",".join(fields)
