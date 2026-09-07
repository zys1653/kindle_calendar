"""Pure mailbox layout, sharing the application's grayscale typography."""
from datetime import datetime
from .mail import FOLDERS
from .weather import CHINA


def address(value):
    entry = (value or {}).get('emailAddress', {})
    return entry.get('name') or entry.get('address') or '未知'


def date_label(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(CHINA).strftime('%m-%d %H:%M')
    except (ValueError, AttributeError):
        return '时间未知'


def body_layout(detail, path, width, height):
    from .render import modal_lines
    lines = modal_lines(detail.get('text', '正文尚未加载；联网后点击“重试正文”。'), path, width-24)
    capacity = max(1, (height-650)//36)
    return lines, capacity, max(1, (len(lines)+capacity-1)//capacity)


def draw_mail(c, app, x, right, h):
    from .render import timestamp, font
    width = right-x
    detail = app.mail_detail
    if detail:
        c.button((x, 180, x+165, 240), '返回列表', ('mail_back',), size=25)
        pending = next((q for q in app.mail_queue if q['id'] == detail['id']), None)
        if pending:
            label = '取消失败操作' if pending['state'] == 'failed' else '取消待提交'
            c.button((right-210, 180, right, 240), label, ('mail_cancel', detail['id']), size=24)
        elif not detail.get('isRead'):
            c.button((right-180, 180, right, 240), '标为已读', ('mail_read', detail['id']), size=25)
        c.lines((x, 258), detail.get('subject') or '（无主题）', width, 32, 2)
        recipients = '、'.join(address(t) for t in detail.get('toRecipients', [])) or '未填写'
        c.lines((x, 350), '发件人：' + address(detail.get('from')), width-220, 21, 1)
        c.text((right-195, 350), date_label(detail.get('receivedDateTime')), 22)
        c.lines((x, 386), '收件人：' + recipients, width, 21, 1)
        status = ('已读待提交' if pending and pending['state'] == 'pending' else pending.get('error', '') if pending else '已读' if detail.get('isRead') else '未读')
        c.lines((x, 422), status + (' · 有附件（请在 Outlook 查看）' if detail.get('hasAttachments') else ''), width-190, 20, 1)
        if 'text' not in detail:
            c.button((right-180, 415, right, 465), '重试正文', ('mail_open', detail['id']), size=23)
        c.draw.line((x, 475, right, 475), fill=0, width=2)
        lines, capacity, pages = body_layout(detail, c.path, width, h)
        page = min(app.mail_body_page, pages-1)
        for i, line in enumerate(lines[page*capacity:(page+1)*capacity]):
            c.text((x+8, 488+i*36), line, 26)
        label = '第 {} / {} 页'.format(page+1, pages)
    else:
        c.text((x, 179), '邮箱', 38)
        c.lines((x+110, 192), app.mail_account or '尚未授权', width-340, 23, 1)
        c.button((right-200, 180, right, 240), '同步邮箱', ('mail_sync',), size=26)
        cache = app.mail_cache
        stale = bool(cache.get('synced') and app.config['mail_minutes'] and app.now.timestamp()-cache['synced'] > app.config['mail_minutes']*60)
        status = cache.get('error') or ('缓存已过期' if stale else '最近同步 ' + timestamp(cache.get('synced', 0)))
        if any(k in app.jobs for k in ('mail', 'mail_more')):
            status = '正在同步 · 可继续阅读缓存'
        c.lines((x, 248), status, width, 20, 1)
        columns = 6 if width > 1000 else 3
        tab_w = (width-12*(columns-1))/columns
        for i, (key, title) in enumerate(FOLDERS):
            tx, ty = x+(i%columns)*(tab_w+12), 289+(i//columns)*62
            c.button((tx, ty, tx+tab_w, ty+50), title, ('mail_folder', key), key == app.mail_folder, 23)
        top = 355 if columns == 6 else 417
        row_h = (h-155-top)/app.mail_capacity
        items = cache['items']
        page = min(app.mail_page, max(0, (len(items)-1)//app.mail_capacity))
        if not items:
            c.text((x+25, top+70), '本批暂无邮件，请继续翻页' if cache.get('next') else '暂无邮件' if cache.get('synced') else '尚未获取邮件', 34)
            if not app.mail_ready:
                c.button((x+25, top+135, x+305, top+200), '登录微软账户', ('login',))
        for i, item in enumerate(items[page*app.mail_capacity:(page+1)*app.mail_capacity]):
            y = top+i*row_h
            unread = not item.get('isRead', False)
            if unread:
                c.draw.ellipse((x+3, y+15, x+13, y+25), fill=0)
            who = address(item.get('from'))
            if app.mail_folder in ('sentitems', 'drafts'):
                who = '收件人：' + ('、'.join(address(t) for t in item.get('toRecipients', [])) or '未填写')
            c.lines((x+28, y+4), who, width-260, 22, 1)
            c.text((right-193, y+4), date_label(item.get('receivedDateTime')), 22)
            subject = item.get('subject') or '（无主题）'
            # Slight stroke strengthens unread subjects without requiring a second font.
            from .render import wrap
            title = wrap(subject, font(c.path, 27), width-45)[0]
            if len(title) < len(subject):
                title = title[:-1]+'…'
            c.draw.text((x+28, y+38), title, font=font(c.path, 27), fill=0, stroke_width=1 if unread else 0)
            preview = item.get('bodyPreview') or '（无摘要）'
            if any(q['id'] == item['id'] for q in app.mail_queue):
                preview = '［已读待提交／需处理］' + preview
            c.lines((x+28, y+77), preview, width-45, 20, 2)
            c.draw.line((x+28, y+row_h-7, right, y+row_h-7), fill=185)
            c.hits.append(((x, y, right, y+row_h-8), ('mail_open', item['id'])))
        label = ('第 {} 页 · 后面还有'.format(page+1) if cache.get('next') else '第 {} / {} 页'.format(page+1, max(1, (len(items)+app.mail_capacity-1)//app.mail_capacity)))
    c.button((x, h-132, x+150, h-72), '上一页', ('mail_page', -1), size=25)
    c.text((x+180, h-119), label, 24)
    c.button((right-150, h-132, right, h-72), '下一页', ('mail_page', 1), size=25)
