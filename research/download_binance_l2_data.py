import os
import urllib.request
import zipfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
L2_DIR = os.path.join(DATA_DIR, "l2_deep")

def download_binance_vision_data(symbol="BTCUSDT", days=120):
    """
    Downloads historical order book snapshots (bookTicker) and aggTrades
    from Binance Vision (data.binance.vision).
    While not true L3, this is the highest fidelity free data available directly from Binance.
    """
    print(f"--- Blueprint V6: Downloading {days} days of High-Frequency L2 Data for {symbol} ---")
    
    if not os.path.exists(L2_DIR):
        os.makedirs(L2_DIR)
        
    end_date = datetime.utcnow().date() - timedelta(days=1)
    
    for i in range(days):
        target_date = end_date - timedelta(days=i)
        date_str = target_date.strftime("%Y-%m-%d")
        
        # Example URL for aggTrades (tick level executions)
        # https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2023-10-01.zip
        base_url = "https://data.binance.vision/data/spot/daily/bookTicker"
        filename = f"{symbol}-bookTicker-{date_str}.zip"
        url = f"{base_url}/{symbol}/{filename}"
        
        save_path = os.path.join(L2_DIR, filename)
        
        if os.path.exists(save_path.replace(".zip", ".csv")):
            print(f"[{date_str}] Already downloaded and extracted.")
            continue
            
        print(f"[{date_str}] Downloading {url}...")
        try:
            urllib.request.urlretrieve(url, save_path)
            
            # Extract
            with zipfile.ZipFile(save_path, 'r') as zip_ref:
                zip_ref.extractall(L2_DIR)
                
            # Remove zip
            os.remove(save_path)
            print(f"[{date_str}] Success.")
            
        except urllib.error.HTTPError as e:
            print(f"[{date_str}] Data not available on Binance Vision yet (HTTP {e.code}).")
        except Exception as e:
            print(f"[{date_str}] Error: {e}")

if __name__ == "__main__":
    download_binance_vision_data(days=3) # Limit to 3 days for testing purposes
    print("\nTo get the full 120 days, change days=120 in the script.")
