"""Constants for the Lidl Plus integration."""

DOMAIN = "lidl_plus"

CONF_REFRESH_TOKEN = "refresh_token"
CONF_COUNTRY = "country"
CONF_LANGUAGE = "language"
# Option: store keys whose offers are loaded, without it the most visited store is used
CONF_OFFER_STORES = "offer_stores"
# Option: private API key of BestTime.app for the busy hours of the store
CONF_BESTTIME_API_KEY = "besttime_api_key"
# A forecast of BestTime.app is created again after this number of days (BestTime recommends 2 to 4 weeks)
BESTTIME_REFRESH_DAYS = 21
# Option: persons whose locations show the shopping duration, without it all persons
CONF_VISIT_ENTITIES = "visit_entities"

DEFAULT_SCAN_INTERVAL_HOURS = 6
FREQUENTLY_BOUGHT_LIMIT = 10
PRICE_HISTORY_LENGTH = 20
LOG_LENGTH = 50

# Coordinator data keys
KEY_CURRENT_MONTH_SPENDING = "current_month_spending"
KEY_CURRENT_MONTH_START = "current_month_start"
KEY_AVERAGE_BASKET = "average_basket"
KEY_SHOPPING_FREQUENCY = "shopping_frequency_days"
KEY_SPENDING_BY_MONTH = "spending_by_month"
KEY_SPENDING_BY_STORE = "spending_by_store"
KEY_TOTAL_SPENT = "total_spent"
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
KEY_LOG = "log"
KEY_PRODUCTS = "products"
KEY_RECEIPTS = "receipts"
KEY_SAVINGS_TOTAL = "savings_total"
KEY_SAVINGS_MONTH = "savings_current_month"
KEY_SAVINGS_BY_MONTH = "savings_by_month"
KEY_STORES = "stores"
KEY_OFFER_STORES = "offer_stores"
KEY_OFFERS = "offers"
KEY_OFFERS_CURRENT = "offers_current"
KEY_OFFERS_UPCOMING = "offers_upcoming"
KEY_OFFERS_FOR_YOU = "offers_for_you"
KEY_LEAFLETS = "leaflets"
# Offer region whose weekly leaflets are shown, the leaflets differ from region to region
KEY_LEAFLET_REGION = "leaflet_region"
# Busy hours of the store: opening hours, own shopping times and the forecast of BestTime.app
KEY_BUSY_TIMES = "busy_times"
# Changes with every update of the data, also when only offers or leaflets changed
KEY_DATA_VERSION = "data_version"
# Every article known from the receipts, offers and leaflets by article key (see _lidlplus.articles)
KEY_CATALOG = "catalog"
# How long the shopping took: statistics of the visits of the stores (the receipts have their visit)
KEY_SHOPPING_DURATION = "shopping_duration"

# Services
SERVICE_ACTIVATE_ALL_COUPONS = "activate_all_coupons"
SERVICE_SYNC = "sync"
SERVICE_EXPORT = "export"
# Default folder of the export service, inside the config folder (not served, unlike www)
EXPORT_DIR = "lidl_plus_export"

# Sidebar panel
PANEL_URL_PATH = "lidl-plus"
PANEL_TITLE = "Lidl Plus"
PANEL_ICON = "mdi:cart"
PANEL_WEBCOMPONENT = "lidl-plus-panel"
STATIC_URL_PATH = "/lidl_plus_frontend"
