def add_text_layer(layers, new_text, text_rgb, stroke_rgb, font_name, width, height):
    """Добавляет текстовый слой ПОВЕРХ всех, не удаляя старые."""
    center_x = width / 2.0
    center_y = height / 2.0
    font_size = 300
    line_height = font_size
    stroke_width = 3
    scale = 100

    # Берём временные параметры из первого слоя (чтобы текст анимировался вместе с анимацией)
    ref_layer = None
    for layer in layers:
        if "ip" in layer and "op" in layer:
            ref_layer = layer
            break
    if ref_layer:
        ip = ref_layer.get("ip", 0)
        op = ref_layer.get("op", 180)
        st = ref_layer.get("st", 0)
    else:
        ip, op, st = 0, 180, 0

    text_layer = {
        "ty": 5,
        "nm": "Generated Text (overlay)",
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [center_x, center_y, 0]},
            "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [scale, scale, 100]}
        },
        "t": {
            "d": {
                "k": [
                    {
                        "s": {
                            "f": font_name,
                            "t": new_text,
                            "j": 1,
                            "tr": 0,
                            "lh": line_height,
                            "ls": 0,
                            "s": font_size,
                            "fc": text_rgb,
                            "sc": stroke_rgb,
                            "sw": stroke_width,
                            "of": 0
                        }
                    }
                ]
            }
        },
        "ip": ip,
        "op": op,
        "st": st,
        "bm": 0
    }
    # Добавляем в КОНЕЦ (поверх всех)
    layers.append(text_layer)
    return layers

def replace_text_and_colors(data, new_text, text_color_hex, fill_color_hex, stroke_color_hex, font_name="Arial-Bold"):
    text_rgb = hex_to_rgb(text_color_hex)
    fill_rgb = hex_to_rgb(fill_color_hex)
    stroke_rgb = hex_to_rgb(stroke_color_hex)

    # Меняем цвета во всех слоях (это не сломает анимацию)
    data = find_and_replace_colors(data, text_rgb, fill_rgb, stroke_rgb)

    if "layers" in data:
        width = data.get("w", 512)
        height = data.get("h", 512)
        # НЕ удаляем старые текстовые слои, просто добавляем свой поверх
        data["layers"] = add_text_layer(data["layers"], new_text, text_rgb, stroke_rgb, font_name, width, height)

    data = ensure_fonts(data, font_name)
    return data, True
