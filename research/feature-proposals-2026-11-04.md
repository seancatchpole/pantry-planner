# Feature proposals for pantry-planner — market research
Date: 2026-11-04. Research only; nothing implemented.
Compared against: SuperCook, Grocy, NoWaste, Plan to Eat, Paprika, Mealie, Tandoor,
Samsung Food, AnyList, PlateJoy, Eat Your Books, Fango/Frida/Sortter, Forked (Mealie),
ThePantry, Strongr Fastr.

## Cooking-from-inventory
1. "Cook Right Now" reverse search — list every recipe makeable from current stock,
   ranked by fewest missing items. Model: https://www.supercook.com/ (11M-recipe scale).
   Our planner already computes per-recipe pantry coverage; this exposes it as a page/API.
2. Expiry-date tracking + "cook soon" boost — optional expiry per inventory row; planner
   prioritizes meals using soon-to-expire stock; Telegram heads-up.
   Models: https://github.com/grocy/docs/blob/master/tutorials/food.md ,
   https://www.nowasteapp.com/post/reduce-food-waste-with-expiry-tracking/
3. Food-waste log — record what got thrown out; recurring waste de-prioritizes that
   ingredient class. Model: https://www.nowasteapp.com/home/
4. Leftovers logic — batch-cook recipes auto-suggest a leftovers night so plans don't
   collide with reheats. Model: https://www.plantoeat.com/tour/meal-planner/

## Plan interaction
5. Drag-and-drop / swap-a-night UI — click a planned night to pick/shuffle alternatives
   in place, re-score the rest. Models: https://samsungfood.com/meal-planner/ ,
   https://www.paprikaapp.com/
6. iCal export of weekly plan (subscribe from phone calendar).
   Model: https://www.plantoeat.com/
7. Cook mode — full-screen step view, tap-through checkboxes, timers, big text.
   Model: https://docs.mealie.io/documentation/getting-started/features/
8. Servings scaler — scale quantities and protein/serving (our ingredients are already
   parsed qty/unit/name). Models: https://docs.mealie.io/ , https://www.paprikaapp.com/
9. Shareable read-only week link (token URL, no auth).
   Model: https://docs.mealie.io/documentation/getting-started/features/ (shared mealplans)
10. "Surprise me" pick — random pick from top of scored candidate pool.
    Model: https://docs.mealie.io/

## Inventory capture
11. Receipt review queue in the web UI — scanned items land pending with confidence
    flags ("Needs ID") to approve/edit before entering stock. Today intake runs through
    chat. Model: https://fango.fi/en/blog/best-pantry-inventory-app/
12. Barcode / Open Food Facts lookup for bag photos and non-receipt items.
    Models: https://grocy.info/ ,
    https://www.reddit.com/r/selfhosted/comments/1rme1z4/thepantry_selfhosted_food_inventory_management/
13. Closed-loop shopping list — checking items off at the store auto-adds them to
    pantry; per-store split (Costco vs Giant Eagle) with aisle order + location
    reminders. Models: https://www.anylist.com/features , https://www.paprikaapp.com/help/android/
14. Cost-per-meal + weekly food spend — receipts already carry prices; link receipt
    lines to inventory items, roll up cost/dinner. Model:
    https://apps.apple.com/us/app/grocy-pantry-food-inventory/id1473757050

## Preference learning & nutrition
15. Micro-tags on feedback — one-tap tags beside thumbs (kid-approved, too much work,
    too salty, make again with X) feeding the planner like thumbs do.
    Models: https://support.platejoy.com/ , https://www.eatyourbooks.com/
16. "Our Hits" shelf — favorites collection from cooked history; planner bonus for
    members. Models: https://help.cookshelf.app/article/293-i-used-eat-your-books-what-changes ,
    https://play.google.com/store/apps/details?id=com.hindsightlabs.paprika.android.v3
17. Weekly nutrition rollup — protein/calorie totals per dinner and week vs targets
    (beyond the >=30g gate). NYT nutrition metadata already stored.
    Models: https://www.plantoeat.com/blog/2024/01/level-up-your-meal-planning-with-these-features/ ,
    https://www.strongrfastr.com/macro-meal-planner
18. Kid-specific signal — lightweight "kid-ate-it?" flag separate from adult thumbs;
    weeknight scoring weights it. Full multi-member households (Mealie) is overkill.
    Model: https://docs.mealie.io/documentation/getting-started/features/

## Platform & reach
19. Installable PWA + offline tonight's-recipe cache on the phone.
    Demand signal: https://apps.apple.com/us/app/forked-recipes/id6760947117
20. Home Assistant integration — low-stock/expiry sensors + "tonight's dinner" card.
    Pattern: https://grocy.info/ (Grocy HA add-on); we already run HA.

## Priority judgment
Best bang-for-buck: 1, 5, 11, 2, 15 — all build on data the app already captures.
Weakest fits: 12 (we intake via receipts, not unit scanning) and full multi-household
accounts.
