import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("CSFLOAT_API_KEY")

def check_single_price(item_name):
    print(f"Searching market for: {item_name}")
    url = "https://csfloat.com/api/v1/listings"
    headers = {"Authorization": API_KEY}
    params = {
        "market_hash_name": item_name,
        "limit": 1,
        "sort_by": "lowest_price",
        "state": "listed",
        "type": "buy_now",
        "paint_seed": 651,
        "max_float": 0.1,
    }
    
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code == 200:
        raw_data = response.json()
        
        # Check if the response is a list or a dict
        listings = []
        if isinstance(raw_data, list):
            listings = raw_data
        elif isinstance(raw_data, dict):
            # Try to find where the listings are hidden (usually in 'data' or 'listings')
            listings = raw_data.get('data', raw_data.get('listings', []))

        if listings:
            cheapest_item = listings[0]
            price_usd = cheapest_item.get('price', 0) / 100
            print(f"\n✅ Success! Found {item_name}")
            print(f"Current Floor Price: ${price_usd:.2f}")
        else:
            print(f"\n❌ No active listings found for '{item_name}'.")
            print(f"API returned keys: {raw_data.keys() if isinstance(raw_data, dict) else 'List format'}")
    else:
        print(f"❌ API Error: {response.status_code}")
        print(f"Response: {response.text}")

if __name__ == "__main__":
    check_single_price("★ Skeleton Knife | Fade (Factory New)")