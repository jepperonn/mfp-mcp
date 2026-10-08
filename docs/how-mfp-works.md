# How MyFitnessPal works (as far as this server needs to know)

MyFitnessPal has **no public API**. Everything below was observed in the browser and verified against a live account
(last verified **2026-10-08**). It can change without notice. When something breaks, start here and with the
[live smoke test](../tests/live/).

## Authentication

There are two hosts with two kinds of auth:

| Host | Used for | Auth |
|---|---|---|
| `https://www.myfitnesspal.com` | token, keep-alive, food search | the browser's cookies (the whole `cookie:` header, ~25 cookies) |
| `https://api.myfitnesspal.com` | diary, foods, goals, user | `Authorization: Bearer <token>` |

**A single session cookie is not enough.** Send the full cookie header. With too few cookies the web host answers
`302 → /account/login?callbackUrl=…`, which the server treats as "session lost".

### Bearer token

`GET www.myfitnesspal.com/user/auth_token?refresh=true` (cookies, `Accept: application/json`) →

```json
{"token_type": "Bearer", "access_token": "…", "expires_in": 864000, "refresh_token": "…", "user_id": "…"}
```

The token lives ~10 days. Every API call needs these headers:

```
Authorization: Bearer <access_token>
mfp-client-id: mfp-main-js
mfp-user-id: <user_id>
Accept: application/json
User-Agent: <a normal desktop Chrome UA>
```

If the response is not JSON, the cookies are no longer logged in.

### Keeping the session alive

- `GET /user/auth_token?refresh=true` (and `GET /food/diary`) answer with `Set-Cookie` that renews
  `last_login_date` (+30 days), `_mfp_session` and `__cf_bm`. The server calls it every 6 hours and saves every
  renewed cookie. A session that is used therefore keeps going.
- **Do not use `GET /api/auth/session` as a keep-alive.** With an old NextAuth token it returns `{}` and
  `Set-Cookie: __Secure-next-auth.session-token=; Max-Age=0`, deleting the cookie.
- Never persist a cookie the server deletes (`Max-Age=0`, empty value, or an `Expires` in the past).
- Unknown: whether there is a hard maximum session age. `status` shows the `last_login_date` expiry.

## Diary (JSON API)

| What | Request | Response |
|---|---|---|
| Totals per meal | `GET /v2/diary?entry_date=YYYY-MM-DD` | `{"items": [{"type": "diary_meal", "diary_meal": "Breakfast", "nutritional_contents": {…}}]}` |
| Entries | `GET /v2/diary?entry_date=YYYY-MM-DD&types=food_entry` | `{"items": [food_entry, …]}` (see below) |
| Add | `POST /v2/diary` | `201 {"items": […]}` |
| Change servings | `PATCH /v2/diary/{entry_id}` body `{"item": {"servings": 2.0}}` | `204` |
| Delete | `DELETE /v2/diary/{entry_id}` | `204` |

`OPTIONS /v2/diary` lists `GET, POST, PUT, PATCH, DELETE`.

A `food_entry` looks like:

```
id, master_id, type: "food_entry", date, meal_name: "Breakfast", meal_position: 0,
food: {id, version, brand_name, description, serving_sizes: [...], nutritional_contents: {...}},
serving_size: {id, value, unit, nutrition_multiplier, gram_weight, is_fraction},
servings, nutritional_contents: {energy: {unit: "calories", value}, protein, carbohydrates, fat, ...}
```

### Adding an entry

```json
{"items": [{
  "type": "food_entry",
  "date": "2026-10-08",
  "meal_name": "Snacks",
  "meal_position": 3,
  "food": {"id": "<food id>", "version": "<food version>"},
  "serving_size": {"value": 1.0, "unit": "Serving", "nutrition_multiplier": 1.0},
  "servings": 1.0
}]}
```

The validation is strict:

- `food` must contain **only** `id` and `version`. A full food object returns `400 found unpermitted parameters`.
- `serving_size` must contain **only** `value`, `unit` and `nutrition_multiplier`. Don't copy `id`,
  `gram_weight` or `is_fraction` from a food's `serving_sizes`.
- Meals: `0 Breakfast`, `1 Lunch`, `2 Dinner`, `3 Snacks`.
- `PATCH` with `{"servings": 2}` (without `item`) returns `400 No item(s) found`.
- Not verified: moving an entry to another meal with `PATCH`.

## Foods

| What | Request |
|---|---|
| Food by id | `GET /v2/foods/{id}` → `{"item": {id, version, description, brand_name, serving_sizes[], nutritional_contents}}` |
| Create a private custom food | `POST /v2/foods` (below) → `200 {"item": {"id": …}}` |
| Delete a custom food | `DELETE /v2/foods/{id}` → `204` |

```json
{"item": {
  "description": "5 eggs", "brand_name": "", "public": false,
  "serving_sizes": [{"value": 1.0, "unit": "serving", "nutrition_multiplier": 1.0}],
  "nutritional_contents": {"energy": {"unit": "calories", "value": 400}, "protein": 32, "carbohydrates": 2, "fat": 28}
}}
```

`GET /v2/foods?…` only accepts `ids`. There is no search there (`422`).

## Search (web scrape)

No JSON search endpoint was found (`/v2/foods/search`, `/v2/search…` → 404/422). The server uses the website:

1. `GET /food/search` and take `input[name=authenticity_token]`.
2. `POST /food/search` with `authenticity_token`, `search=<query>`, `date`, `meal=0`.
3. Parse each `li.matched-food`:
   - the id is `.search-title-container a[data-external-id]` and the name is the link text;
   - `p.search-nutritional-info` reads `"<brand>, <serving>, <kcal> calories"`.

The ids work directly with `GET /v2/foods/{id}`.

## Goals

`GET /v2/nutrient-goals?date=YYYY-MM-DD` returns
`{"items": [{"valid_from", "valid_to", "daily_goals": [{"day_of_week": "monday", "energy": {"value", "unit"}, "protein", "carbohydrates", "fat", …}], "default_goal": {…}}]}`.
Use the `daily_goals` entry for the weekday, else `default_goal`.

## Other

- `GET /v2/users/{user_id}` → `{"item": {"username", …}}`.
- Cloudflare in front of both hosts has not blocked Fly.io's servers so far.
