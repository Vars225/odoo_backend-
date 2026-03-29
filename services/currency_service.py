import httpx
import logging
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from config import settings

logger = logging.getLogger(__name__)

# In-memory cache: { base_currency: (rates_dict, cached_at) }
_rates_cache: Dict[str, Tuple[dict, datetime]] = {}
_countries_cache: Optional[Dict[str, str]] = None  # { country_name_lower: currency_code }
_CACHE_TTL = timedelta(hours=1)

# Fallback static rates (used if API is unavailable)
FALLBACK_RATES = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "INR": 83.5,
    "JPY": 149.5, "CAD": 1.36, "AUD": 1.53, "SGD": 1.34,
    "AED": 3.67, "CHF": 0.90,
}


async def get_exchange_rates(base_currency: str) -> dict:
    """Fetch and cache exchange rates for a given base currency."""
    base = base_currency.upper()

    # Check cache
    if base in _rates_cache:
        rates, cached_at = _rates_cache[base]
        if datetime.utcnow() - cached_at < _CACHE_TTL:
            return rates

    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{base}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            rates = data.get("rates", {})
            _rates_cache[base] = (rates, datetime.utcnow())
            logger.info(f"✅ Exchange rates cached for base: {base}")
            return rates
    except Exception as e:
        logger.warning(f"⚠️ Exchange rate API failed: {e}. Using fallback rates.")
        # Convert fallback rates to the requested base
        if base in FALLBACK_RATES:
            base_rate = FALLBACK_RATES[base]
            return {k: v / base_rate for k, v in FALLBACK_RATES.items()}
        return FALLBACK_RATES


async def convert_currency(
    amount: float,
    from_currency: str,
    to_currency: str
) -> float:
    """Convert amount from one currency to another."""
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()

    if from_currency == to_currency:
        return round(amount, 2)

    rates = await get_exchange_rates(from_currency)
    rate = rates.get(to_currency)

    if not rate:
        logger.error(f"No rate found for {to_currency}")
        raise ValueError(f"Currency conversion not available for {to_currency}")

    return round(amount * rate, 2)


async def get_country_currency(country_name: str) -> Optional[str]:
    """Get ISO currency code for a country name."""
    global _countries_cache

    if _countries_cache is None:
        await _load_countries()

    country_lower = country_name.lower().strip()
    return _countries_cache.get(country_lower)


async def _load_countries():
    """Load country → currency mapping from RestCountries API."""
    global _countries_cache
    _countries_cache = {}

    # Hardcoded fallback mapping
    fallback = {
        "india": "INR", "united states": "USD", "usa": "USD",
        "united kingdom": "GBP", "uk": "GBP", "germany": "EUR",
        "france": "EUR", "japan": "JPY", "canada": "CAD",
        "australia": "AUD", "singapore": "SGD", "uae": "AED",
        "united arab emirates": "AED", "switzerland": "CHF",
        "china": "CNY", "brazil": "BRL", "south africa": "ZAR",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(settings.restcountries_api)
            response.raise_for_status()
            countries = response.json()

            for country in countries:
                name = country.get("name", {}).get("common", "").lower()
                currencies = country.get("currencies", {})
                if name and currencies:
                    # Take the first currency code
                    currency_code = next(iter(currencies.keys()))
                    _countries_cache[name] = currency_code

            # Also add aliases
            _countries_cache.update({k: v for k, v in fallback.items()
                                      if k not in _countries_cache})
            logger.info(f"✅ Loaded {len(_countries_cache)} country-currency mappings")

    except Exception as e:
        logger.warning(f"⚠️ RestCountries API failed: {e}. Using fallback mapping.")
        _countries_cache = fallback