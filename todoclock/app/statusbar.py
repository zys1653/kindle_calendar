"""Pure status presentation, shared by the header and status details."""


def body_battery(status):
    value = status.get('battery')
    return '本体 ' + ('—' if value is None else str(value)+'%') + (
        ' 充电' if status.get('charging') or status.get('external_power') else '')


def sync_ready(app):
    todo_time = app.todo.get('synced', 0)
    weather_time = app.weather_synced
    if not todo_time or not weather_time or not app.mail_synced or not app.mail_ready:
        return False
    if any(name in app.jobs or name in app.errors for name in ('todo', 'weather', 'flush', 'mail', 'mail_flush')):
        return False
    if app.mail_queue or app.mail_summary.get('error'):
        return False
    if app.outbox or app.weather_view['errors'] or any(x.get('error') for x in app.todo.get('lists', [])):
        return False
    for name, fetched in (('todo', todo_time), ('weather', weather_time), ('mail', app.mail_synced)):
        interval = app.config[name+'_minutes']
        if interval and app.now.timestamp()-fetched > interval*60:
            return False
    return True
