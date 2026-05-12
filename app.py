import os
import time
import re
import requests
import random
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lm_meesho_multi_qty.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- DATABASE MODEL ---
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200))
    platform = db.Column(db.String(50))
    url = db.Column(db.String(500))
    image_path = db.Column(db.String(200))
    
    # 🌐 WEBSITE DATA
    listed_mrp = db.Column(db.String(50))
    net_quantity = db.Column(db.String(100))
    country_of_origin = db.Column(db.String(100))
    manufacturer_address = db.Column(db.String(500))
    web_pid = db.Column(db.String(100), default="Not Listed") 
    web_shelf_life = db.Column(db.String(100), default="Not Listed")  
    
    # 📷 PI CAMERA DATA (Hardware Placeholders)
    pi_mrp = db.Column(db.String(50), default="Check via Pi Camera")
    pi_net_quantity = db.Column(db.String(100), default="Check via Pi Camera")
    pi_country_of_origin = db.Column(db.String(100), default="Check via Pi Camera")
    pi_manufacturer = db.Column(db.String(500), default="Check via Pi Camera")
    pi_pid = db.Column(db.String(100), default="Check via Pi Camera")
    pi_shelf_life = db.Column(db.String(100), default="Check via Pi Camera")
    
    status = db.Column(db.String(50))

with app.app_context():
    db.create_all()

# --- HELPER FUNCTIONS ---
def download_image(url, filename):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            with open(filename, 'wb') as f: f.write(response.content)
            return True
    except: pass
    return False

def find_all_weights(text):
    if not text: return None
    weight_pattern = r'(\d+(?:\.\d+)?\s*(?:kg|gm|g|ml|l|liter|litre))'
    weights = re.findall(weight_pattern, text, re.IGNORECASE)
    
    if weights:
        unique_weights = sorted(list(set([w.lower().replace(" ", "") for w in weights])))
        return ", ".join(unique_weights)

    pcs_pattern = r'(\d+\s*pcs)'
    pcs = re.search(pcs_pattern, text, re.IGNORECASE)
    if pcs: return pcs.group(1).lower()
    return None

def clean_text_value(text):
    if not text: return "Not Listed"
    text = text.replace("Name :", "").replace("Details", "").strip()
    text = text.replace("More Information", "")
    return text

# ==========================================
# MEESHO SCRAPER (SMART EXTRACTION)
# ==========================================
def scrape_meesho_smart(keyword):
    products = []
    
    user_data_dir = os.path.join(os.getcwd(), 'chrome_profile')
    if not os.path.exists(user_data_dir): os.makedirs(user_data_dir)

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir, headless=False,
            viewport={'width': 1366, 'height': 768},
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
        )
        page = browser.new_page()

        try:
            print(f"🔍 Searching Meesho: {keyword}")
            page.goto(f"https://www.meesho.com/search?q={keyword}", timeout=60000)
            
            if "Access Denied" in page.title():
                print("⚠️ Access Denied! Please refresh manually...")
                time.sleep(15)
                
            try: page.wait_for_selector("a[href*='/p/']", timeout=15000)
            except: 
                print("⚠️ No products found.")
                return []

            page.evaluate("window.scrollTo(0, 1500)")
            time.sleep(2)
            
            links = page.query_selector_all("a[href*='/p/']")
            urls = []
            for link in links:
                if len(urls) >= 5: break 
                href = "https://www.meesho.com" + link.get_attribute("href")
                if href not in urls: urls.append(href)
            
            for i, url in enumerate(urls):
                print(f"🕵️ Scanning Item {i+1}...")
                try:
                    page.goto(url, timeout=45000)
                    if "Access Denied" in page.title(): continue
                    page.wait_for_selector("h1", timeout=10000)
                    
                    # 1. Basic Data
                    name = page.locator("h1").first.inner_text().strip()
                    try: price = page.locator("h4:has-text('₹')").first.inner_text().strip()
                    except: price = "N/A"
                    try: img_src = page.locator("div[class*='ProductImage'] img").first.get_attribute("src")
                    except: img_src = ""

                    net_qty = find_all_weights(name)
                    origin = "Not Listed"
                    manufacturer = "Not Listed" 
                    shelf_life = "Not Listed" 
                    pid = "Not Listed"
                    
                    # 2. Extract Shelf Life from Product Highlights
                    try:
                        body_text = page.locator("body").inner_text()
                        body_lines = [line.strip() for line in body_text.split('\n') if line.strip()]
                        
                        for idx, line in enumerate(body_lines):
                            lower_line = line.lower()
                            if "shelf life" in lower_line:
                                if ":" in line:
                                    val = line.split(":")[-1].strip()
                                    if val: shelf_life = clean_text_value(val)
                                elif idx + 1 < len(body_lines):
                                    val = body_lines[idx + 1]
                                    if len(val) < 30: 
                                        shelf_life = clean_text_value(val)
                                break
                    except Exception as e:
                        print("Shelf life extraction error:", e)

                    # 3. Extract Country of Origin from Description
                    try:
                        desc_locator = page.locator("div[class*='ProductDescription']")
                        if desc_locator.count() > 0:
                            full_text = desc_locator.first.inner_text()
                            lines = full_text.split('\n')
                            
                            if not net_qty: net_qty = find_all_weights(full_text)

                            for line in lines:
                                line_lower = line.lower().strip()
                                if "country of origin" in line_lower:
                                    val = line.split(':')[-1].strip()
                                    origin = clean_text_value(val)
                    except: pass

                    # 4. Click 'Additional Details' -> 'More Information' to get PID and Manufacturer
                    try:
                        try:
                            add_details_btn = page.locator("text='Additional Details'").first
                            if add_details_btn.count() > 0 and add_details_btn.is_visible():
                                add_details_btn.click()
                                time.sleep(1.5) 
                        except Exception as e: pass

                        more_btn = page.locator("text='More Information'").first
                        if more_btn.count() > 0 and more_btn.is_visible():
                            more_btn.click()
                            time.sleep(2.5) 
                            
                            if page.locator("div[role='dialog']").count() > 0:
                                all_text = page.locator("div[role='dialog']").inner_text()
                            else:
                                all_text = page.locator("body").inner_text()
                            
                            # ---> 🌟 NEW BULLETPROOF PID REGEX LOGIC 🌟 <---
                            # This regex finds "PID", "Product ID", or "Product Code" even if spacing or punctuation is weird
                            pid_match = re.search(r'(?:PID|Product ID|Product Code)\s*[-:]?\s*([a-zA-Z0-9]+)', all_text, re.IGNORECASE)
                            if pid_match:
                                pid = pid_match.group(1).strip()
                            # -----------------------------------------------

                            lines = [line.strip() for line in all_text.split('\n') if line.strip()]
                            capture = False
                            address_parts = []
                            stop_words = ["importer", "net weight", "weight", "shelf life", "fssai", "item", "brand", "generic"]
                            
                            for line in lines:
                                lower_line = line.lower()

                                if "manufacturer information" in lower_line or "packer information" in lower_line or "manufacturer details" in lower_line or "packer details" in lower_line:
                                    capture = True
                                    continue 
                                
                                if capture:
                                    if any(sw in lower_line for sw in stop_words) and len(line) < 35:
                                        break
                                    if line == "X" or line == "COPY":
                                        break
                                    address_parts.append(line)
                            
                            if address_parts:
                                manufacturer = ", ".join(address_parts)
                            
                            page.keyboard.press("Escape") 
                            time.sleep(1)
                    except Exception as e: pass

                    if not net_qty: net_qty = "Not Listed"

                    # 5. Append extracted data
                    products.append({
                        "name": name, "price": price, "url": url, "image": img_src, "platform": "Meesho",
                        "origin": origin, "net_qty": net_qty, "manufacturer": manufacturer,
                        "shelf_life": shelf_life, 
                        "pid": pid 
                    })
                    time.sleep(random.uniform(2, 4))
                except Exception as e: 
                    print(f"Error on item {i}: {e}")
                    continue
                    
        except Exception as e: print(f"Main Error: {e}")
        browser.close()
    return products

# --- ROUTES ---
@app.route('/')
def dashboard():
    return render_template('dashboard.html', products=Product.query.order_by(Product.id.desc()).all())

@app.route('/details/<int:product_id>')
def view_details(product_id):
    product = Product.query.get_or_404(product_id)
    return render_template('product_details.html', p=product)

@app.route('/run_monitor', methods=['POST'])
def run_monitor():
    keyword = request.form.get('keyword')
    try:
        db.session.query(Product).delete()
        db.session.commit()
    except: db.session.rollback()

    scraped_data = scrape_meesho_smart(keyword)
    
    if not scraped_data:
        return jsonify({"status": "error", "message": "No products found."})

    for idx, p in enumerate(scraped_data):
        img_fn = f"static/images/prod_{int(time.time())}_{idx}.jpg"
        os.makedirs('static/images', exist_ok=True)
        download_image(p['image'], img_fn)
        
        status = "Pending Physical Verification"
        if p['origin'] == "Not Listed" or p['net_qty'] == "Not Listed" or p['manufacturer'] == "Not Listed":
            status = "Non-Compliant (Web Missing Data)"

        db.session.add(Product(
            name=p['name'], platform=p['platform'], url=p['url'],
            image_path=img_fn, listed_mrp=p['price'],
            country_of_origin=p['origin'],
            net_quantity=p['net_qty'],
            manufacturer_address=p['manufacturer'],
            
            # Web dynamically scraped Web PID and Shelf Life
            web_pid=p['pid'], 
            web_shelf_life=p['shelf_life'], 
            
            # Hardware placeholders wait for Pi Cam
            pi_mrp="Check via Pi Camera",
            pi_net_quantity="Check via Pi Camera",
            pi_country_of_origin="Check via Pi Camera",
            pi_manufacturer="Check via Pi Camera",
            pi_pid="Check via Pi Camera",
            pi_shelf_life="Check via Pi Camera",
            
            status=status
        ))
    db.session.commit()
    return jsonify({"status": "success", "message": "Inspection Complete!"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)