path = r'C:\Users\User\Desktop\app\KworkSniper\telegram_bot.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

from_sep = '    preview_text = ('
to_sep = '    await callback.message.reply(preview_text, reply_markup=markup, parse_mode="HTML")'

from_idx = content.find(from_sep)
to_idx = content.find(to_sep, from_idx)

if from_idx >= 0 and to_idx > from_idx:
    replacement = '''    preview_text = (
        f"\\U0001f916 <b>\\u0410\\u0432\\u0442\\u043e\\u043e\\u0442\\u043a\\u043b\\u0438\\u043a</b>\\n\\n"
        f"\\U0001f4cb <b>\\u0417\\u0430\\u043a\\u0430\\u0437:</b> {project.get('title', '\\u2014')}\\n"
        f"\\U0001f4b0 <b>\\u0411\\u044e\\u0434\\u0436\\u0435\\u0442:</b> {project.get('price', '\\u2014')} \\u20bd\\n"
        f"\\U0001f4b5 <b>\\u0426\\u0435\\u043d\\u0430 \\u043e\\u0442\\u043a\\u043b\\u0438\\u043a\\u0430:</b> {suggested_price} \\u20bd\\n\\n"
        f"<b>\\u0422\\u0435\\u043a\\u0441\\u0442 \\u043e\\u0442\\u043a\\u043b\\u0438\\u043a\\u0430:</b>\\n"
        f"{response_text[:1500]}"
    )'''

    content = content[:from_idx] + replacement + '\n' + content[to_idx:]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed preview_text block')
else:
    print(f'Not found: from={from_idx}, to={to_idx}')
