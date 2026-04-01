"""Constants for the Lidl Plus integration."""

DOMAIN = "lidl_plus"

CONF_REFRESH_TOKEN = "refresh_token"
CONF_COUNTRY = "country"
CONF_LANGUAGE = "language"

DEFAULT_SCAN_INTERVAL_HOURS = 6
FREQUENTLY_BOUGHT_LIMIT = 10

# Coordinator data keys
KEY_CURRENT_MONTH_SPENDING = "current_month_spending"
KEY_AVERAGE_BASKET = "average_basket"
KEY_SHOPPING_FREQUENCY = "shopping_frequency_days"
KEY_SPENDING_BY_MONTH = "spending_by_month"
KEY_SPENDING_BY_STORE = "spending_by_store"
KEY_FREQUENTLY_BOUGHT = "frequently_bought"
KEY_RESTOCK_SUGGESTIONS = "restock_suggestions"
KEY_PRICE_CHANGES = "price_changes"
KEY_CATEGORY_FOOD_SPENDING = "category_food_spending"
KEY_CATEGORY_NONFOOD_SPENDING = "category_nonfood_spending"
KEY_TOTAL_TICKETS = "total_tickets"
KEY_COUPONS = "coupons"
KEY_COUPONS_AVAILABLE = "coupons_available"
KEY_COUPONS_ACTIVATED = "coupons_activated"
KEY_LAST_SYNC = "last_sync"
KEY_NEW_TICKETS_LAST_SYNC = "new_tickets_last_sync"
KEY_LOYALTY_ID = "loyalty_id"
KEY_LAST_ERROR = "last_error"
KEY_PRODUCTS = "products"
KEY_RECEIPTS = "receipts"

# Tax type → product category (Germany/Austria/etc.)
TAX_TYPE_FOOD = "B"      # 7 % — Lebensmittel
TAX_TYPE_NONFOOD = "A"   # 19 % — Allgemein

# Services
SERVICE_ACTIVATE_ALL_COUPONS = "activate_all_coupons"
SERVICE_SYNC = "sync"
