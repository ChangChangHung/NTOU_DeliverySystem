"""Start Flask with a new localhost-only checkout demonstration database."""
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from local_acceptance import Configuration, require_server
from tests.mysql_support import create_local_application

config = Configuration.load()
require_server(config)
os.environ.update(config.environment())
server, settings, metadata = create_local_application('checkout')
with server.app.app_context():
    users = [server.Users(user_name=f'checkout_{role}', password=secrets.token_urlsafe(24),
                          user_state=state) for role, state in (
        ('buyer', server.UserStatus.NORMAL), ('a', server.UserStatus.DELIVERER),
        ('b', server.UserStatus.DELIVERER))]
    restaurant = server.Restaurants(restaurant_name='測試餐廳', restaurant_address='測試取餐處')
    server.db.session.add_all([*users, restaurant])
    server.db.session.flush()
    meals = [server.Meals(meal_name=name, restaurant_id=restaurant.restaurant_id, price=price)
             for name, price in (('測試雞肉飯', 80), ('測試紅茶', 25))]
    server.db.session.add_all(meals)
    server.db.session.commit()
    fixture = {'metadata': metadata, 'accounts': dict(zip(('buyer','a','b'), [u.user_id for u in users])),
               'meals': [{'meal_id':m.meal_id,'meal_name':m.meal_name,'price':m.price} for m in meals]}
    folder = ROOT / 'artifacts' / settings.database
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'fixture.json').write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
    print(json.dumps({'folder':str(folder), 'accounts':fixture['accounts']}), flush=True)
server.app.run(host='127.0.0.1', port=5104, debug=False, use_reloader=False)
