# -*- coding: utf-8 -*-
"""Chat Renderer — render chat messages as social media style images."""
import io
import sqlite3
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


# Color palette
BG_COLOR = (24, 24, 46)        # Dark background
DM_BUBBLE = (30, 47, 96)       # Blue for DM (left)
PL_BUBBLE = (37, 80, 61)       # Green for players (right)
SYS_BUBBLE = (50, 50, 50)      # Gray for system
DM_NAME_COLOR = (233, 69, 96)  # Red for DM name
PL_NAME_COLOR = (78, 204, 163) # Green for player name
TEXT_COLOR = (224, 224, 224)    # Light text
TIME_COLOR = (100, 100, 100)   # Dim timestamp
CARD_BG = (22, 33, 62)         # Card background

# Layout
IMG_WIDTH = 800
PADDING = 20
BUBBLE_PAD_X = 14
BUBBLE_PAD_Y = 10
BUBBLE_RADIUS = 12
LINE_HEIGHT = 22
MAX_BUBBLE_WIDTH = 500
NAME_HEIGHT = 20
TIME_HEIGHT = 16
MSG_GAP = 8
GROUP_SIZE = 50


def _get_font(size=16):
    """Get a font, fallback to default if system font not found."""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(text, font, max_width):
    """Wrap text into lines that fit within max_width."""
    lines = []
    for paragraph in text.split('\n'):
        if not paragraph:
            lines.append('')
            continue
        current = ''
        for char in paragraph:
            test = current + char
            bbox = font.getbbox(test)
            if bbox[2] - bbox[0] > max_width:
                if current:
                    lines.append(current)
                current = char
            else:
                current = test
        if current:
            lines.append(current)
    return lines


def _calc_bubble_height(lines, font):
    """Calculate bubble height for given lines."""
    return BUBBLE_PAD_Y * 2 + len(lines) * LINE_HEIGHT


def _draw_rounded_rect(draw, xy, radius, fill):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
    draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
    draw.pieslice([x0, y0, x0 + 2*radius, y0 + 2*radius], 180, 270, fill=fill)
    draw.pieslice([x1 - 2*radius, y0, x1, y0 + 2*radius], 270, 360, fill=fill)
    draw.pieslice([x0, y1 - 2*radius, x0 + 2*radius, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - 2*radius, y1 - 2*radius, x1, y1], 0, 90, fill=fill)


def _render_page(messages, page_num, total_pages, game_name):
    """Render a single page of messages as an image."""
    font = _get_font(15)
    name_font = _get_font(13)
    time_font = _get_font(11)
    title_font = _get_font(18)

    # Pre-calculate layout
    rendered_msgs = []
    total_height = 80  # Title area

    for msg in messages:
        sender = msg.get('from', '?')
        content = msg.get('content', '')
        at = msg.get('at', '')
        is_dm = sender == 'DM'
        is_system = sender == 'SYSTEM'

        # Skip pass messages
        if content.strip() in ('(pass)', '（pass）'):
            continue

        # Truncate timestamp
        time_str = at.split('T')[1][:5] if 'T' in at else at[:5]

        # Wrap text
        lines = _wrap_text(content, font, MAX_BUBBLE_WIDTH - BUBBLE_PAD_X * 2)
        bubble_h = _calc_bubble_height(lines, font)
        msg_height = NAME_HEIGHT + bubble_h + TIME_HEIGHT + MSG_GAP

        rendered_msgs.append({
            'sender': sender,
            'content': content,
            'lines': lines,
            'time': time_str,
            'is_dm': is_dm,
            'is_system': is_system,
            'bubble_h': bubble_h,
            'height': msg_height,
        })
        total_height += msg_height

    # Create image
    img = Image.new('RGB', (IMG_WIDTH, max(total_height + 40, 200)), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Title
    title = f"{game_name} — Chat ({page_num}/{total_pages})"
    draw.text((PADDING, 20), title, fill=TEXT_COLOR, font=title_font)
    draw.text((PADDING, 48), f"{len(rendered_msgs)} messages", fill=TIME_COLOR, font=time_font)
    y = 80

    # Draw messages
    for msg in rendered_msgs:
        if msg['is_system']:
            # System message: centered
            text = f"— {msg['content']} —"
            bbox = font.getbbox(text)
            tx = (IMG_WIDTH - (bbox[2] - bbox[0])) // 2
            draw.text((tx, y), text, fill=TIME_COLOR, font=font)
            y += LINE_HEIGHT + MSG_GAP
            continue

        if msg['is_dm']:
            # DM: left-aligned
            name_x = PADDING
            bubble_x = PADDING
            name_color = DM_NAME_COLOR
            bubble_color = DM_BUBBLE
            align = 'left'
        else:
            # Player: right-aligned
            name_x = IMG_WIDTH - PADDING
            bubble_x = IMG_WIDTH - PADDING - MAX_BUBBLE_WIDTH
            name_color = PL_NAME_COLOR
            bubble_color = PL_BUBBLE
            align = 'right'

        # Name
        if align == 'left':
            draw.text((name_x, y), msg['sender'], fill=name_color, font=name_font)
        else:
            bbox = name_font.getbbox(msg['sender'])
            draw.text((name_x - (bbox[2] - bbox[0]), y), msg['sender'], fill=name_color, font=name_font)

        y += NAME_HEIGHT

        # Bubble
        text_width = max(font.getbbox(line)[2] - font.getbbox(line)[0] for line in msg['lines']) if msg['lines'] else 100
        bubble_w = min(text_width + BUBBLE_PAD_X * 2, MAX_BUBBLE_WIDTH)

        if align == 'right':
            bubble_x = IMG_WIDTH - PADDING - bubble_w

        _draw_rounded_rect(draw,
            (bubble_x, y, bubble_x + bubble_w, y + msg['bubble_h']),
            BUBBLE_RADIUS, bubble_color)

        # Text
        ty = y + BUBBLE_PAD_Y
        for line in msg['lines']:
            draw.text((bubble_x + BUBBLE_PAD_X, ty), line, fill=TEXT_COLOR, font=font)
            ty += LINE_HEIGHT

        y += msg['bubble_h']

        # Timestamp
        if align == 'left':
            draw.text((PADDING, y), msg['time'], fill=TIME_COLOR, font=time_font)
        else:
            bbox = time_font.getbbox(msg['time'])
            draw.text((IMG_WIDTH - PADDING - (bbox[2] - bbox[0]), y), msg['time'], fill=TIME_COLOR, font=time_font)

        y += TIME_HEIGHT + MSG_GAP

    return img.crop((0, 0, IMG_WIDTH, min(y + 20, img.height)))


def render_chat_images(game_name: str, room: str = "酒馆大厅") -> bytes:
    """Render chat messages as images and return a ZIP file in bytes."""
    db_path = Path("dm_memory") / game_name / "chat.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Chat database not found: {db_path}")

    # Read messages
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            """SELECT COALESCE(u.name, 'SYSTEM'), m.content, m.created_at
               FROM messages m LEFT JOIN users u ON m.user_id = u.id
               JOIN rooms r ON m.room_id = r.id
               WHERE r.name = ?
               ORDER BY m.id""",
            (room,),
        ).fetchall()

    messages = [{"from": r[0], "content": r[1], "at": r[2]} for r in rows]

    # Filter out pass messages
    filtered = [m for m in messages if m['content'].strip() not in ('(pass)', '（pass）')]

    if not filtered:
        raise ValueError("No messages to render")

    # Split into pages
    pages = []
    for i in range(0, len(filtered), GROUP_SIZE):
        pages.append(filtered[i:i + GROUP_SIZE])

    # Render and pack into ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, page_msgs in enumerate(pages):
            img = _render_page(page_msgs, i + 1, len(pages), game_name)
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            zf.writestr(f"chat_{i+1:03d}.png", img_bytes.getvalue())

    zip_buffer.seek(0)
    return zip_buffer.getvalue()
