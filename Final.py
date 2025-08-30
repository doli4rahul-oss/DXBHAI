from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import qrcode
import io

# 🔹 Bot Token
BOT_TOKEN = "7968135497:AAFOb3uFFOhyWg3z3dhMJ6lbeDzDzvISXr0"

# 🔹 Admin Telegram ID (replace with your actual admin ID)
ADMIN_ID = 5794682979  # Replace with your numeric Telegram user ID

# 🔹 UPI Details for dynamic QR (replace with your actual details)
UPI_ID = "rahul152567@ibl"  # Replace with your UPI ID
PAYEE_NAME = "DXBHAI"  # Replace with your payee name

# 🔹 Google Sheets Setup
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDS = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)
CLIENT = gspread.authorize(CREDS)

# 🔹 Sheets
PRODUCTS_SHEET = CLIENT.open("Products").sheet1  # Sheet1: Products
ACCOUNTS_SHEET = CLIENT.open("Products").worksheet("IRCTC_Accounts Sheet")  # Sheet2
PAYMENTS_SHEET = CLIENT.open("Products").worksheet("Payments Sheet")  # Sheet3


# ---------------- HELPER FUNCTIONS ---------------- #

def get_products():
    """Sheet se product list laata hai"""
    return PRODUCTS_SHEET.get_all_records()

def get_product_details(name):
    """Ek product ke details aur stock"""
    data = PRODUCTS_SHEET.get_all_records()
    for row in data:
        if row["Product Name"].lower() == name.lower():
            return row
    return None


# ---------------- HANDLERS ---------------- #

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = get_products()

    if not products:
        await update.message.reply_text(
            "❌ No products found in Google Sheet.\n\n"
            "⚠️ Please check your sheet headers:\n"
            "`Product Name | Details | Price | Stock`",
            parse_mode="Markdown"
        )
        return

    keyboard = [
        [InlineKeyboardButton(p["Product Name"], callback_data=f"product_{p['Product Name']}")]
        for p in products
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📋 *Available Products:*", reply_markup=reply_markup, parse_mode="Markdown")


# /test command (debug)
async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = PRODUCTS_SHEET.get_all_records()
    await update.message.reply_text(f"📝 Data from sheet:\n{data}")


# Handle messages (quantity input or payment proof)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'state' in context.user_data and context.user_data['state'] == 'waiting_quantity' and update.message.text:
        # Handle quantity input
        try:
            qty = int(update.message.text)
            product = context.user_data['pending_product']
            details = get_product_details(product)
            stock = int(details['Stock'])
            if qty < 1 or qty > stock:
                await update.message.reply_text(f"❌ Invalid quantity. Must be between 1 and {stock}.")
                return
            price = float(details['Price'])
            total = price * qty
            # Generate UPI URI
            uri = f"upi://pay?pa={UPI_ID}&pn={PAYEE_NAME}&am={total}&cu=INR&tn=Payment for {qty} x {product}"
            # Generate QR code
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(uri)
            qr.make(fit=True)
            img = qr.make_image(fill='black', back_color='white')
            # Save to bytes
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            # Send QR
            await update.message.reply_photo(photo=buf, caption=f"💳 Scan & Pay ₹{total}")
            await update.message.reply_text("✅ After payment, send screenshot or UTR number here.")
            # Set state for payment
            context.user_data['pending_quantity'] = qty
            context.user_data['payment_state'] = True
            del context.user_data['state']
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number.")
        return

    if not context.user_data.get('payment_state'):
        return

    product = context.user_data['pending_product']
    qty = context.user_data.get('pending_quantity', 1)
    user_id = update.message.from_user.id
    proof = ""

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Approve", callback_data=f"approve_{user_id}_{product}"),
         InlineKeyboardButton("Reject", callback_data=f"reject_{user_id}_{product}")]
    ])

    if update.message.photo:
        proof = "Screenshot"
        sent_message = await context.bot.send_photo(
            ADMIN_ID,
            photo=update.message.photo[-1].file_id,
            caption=f"Payment proof for {qty} x {product} from user {user_id}\nApprove or Reject?",
            reply_markup=reply_markup
        )
        context.user_data['admin_message_id'] = sent_message.message_id
        context.user_data['is_photo'] = True
    elif update.message.text:
        proof = update.message.text
        sent_message = await context.bot.send_message(
            ADMIN_ID,
            text=f"Payment proof (UTR): {proof} for {qty} x {product} from user {user_id}\nApprove or Reject?",
            reply_markup=reply_markup
        )
        context.user_data['admin_message_id'] = sent_message.message_id
        context.user_data['is_photo'] = False
    else:
        await update.message.reply_text("Please send a screenshot or UTR text.")
        return

    # Log to Payments Sheet (columns: UserID, Product, Quantity, Proof, Status)
    row = [user_id, product, qty, proof, "pending"]
    PAYMENTS_SHEET.append_row(row)

    await update.message.reply_text("Your payment proof has been sent to the admin for verification.")

    # Clear state except for admin_message_id and is_photo
    context.user_data.pop('payment_state', None)
    context.user_data.pop('pending_product', None)
    context.user_data.pop('pending_quantity', None)


# Button clicks
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Product selected
    if query.data.startswith("product_"):
        product_name = query.data.replace("product_", "")
        details = get_product_details(product_name)

        if details:
            msg = f"""📦 *{details['Product Name']}*

📝 {details['Details']}

💰 Price: ₹*{details['Price']}*/-

📦 Stock: *{details['Stock']}* left
"""
            keyboard = [
                [InlineKeyboardButton("💳 Buy Now", callback_data=f"buy_{product_name}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="back_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await query.message.reply_text("❌ Product not found.")

    # Back to main list
    elif query.data == "back_main":
        products = get_products()
        keyboard = [
            [InlineKeyboardButton(p["Product Name"], callback_data=f"product_{p['Product Name']}")]
            for p in products
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("📋 *Available Products:*", reply_markup=reply_markup, parse_mode="Markdown")

    # Start buy process (ask for quantity)
    elif query.data.startswith("buy_"):
        product_name = query.data.replace("buy_", "")
        details = get_product_details(product_name)
        if details and int(details['Stock']) > 0:
            await query.message.reply_text(
                f"📲 How many *{product_name}* do you want to buy? (1 - {details['Stock']})",
                parse_mode="Markdown"
            )
            context.user_data['pending_product'] = product_name
            context.user_data['state'] = 'waiting_quantity'
        else:
            await query.message.reply_text("❌ Out of stock or product not found.")

    # Approve
    elif query.data.startswith("approve_"):
        parts = query.data.split("_")
        action = parts[0]
        user_id = int(parts[1])
        product = "_".join(parts[2:])  # Handle product names with underscores if any

        # Find the payment row to get qty
        pay_data = PAYMENTS_SHEET.get_all_records()
        qty = 1  # Default
        payment_row_index = None
        for k, pay in enumerate(pay_data, start=2):
            if pay['UserID'] == user_id and pay['Product'] == product and pay['Status'] == 'pending':
                qty = int(pay['Quantity'])
                payment_row_index = k
                break

        if payment_row_index is None:
            await query.edit_message_text(text="❌ Error: Payment record not found.")
            return

        # Find available accounts
        data = ACCOUNTS_SHEET.get_all_records()
        if not data:
            await query.edit_message_text(text="❌ Error: IRCTC_Accounts Sheet is empty.")
            return

        delivered_accounts = []
        row_indices = []
        for i, row in enumerate(data, start=2):
            if row['Status'].lower() == 'available':
                delivered_accounts.append(f"Username: {row['Username']}\nPassword: {row['Password']}\nEmail: {row['Email']}\nEmail Password: {row['Email Password']}")
                row_indices.append(i)
                if len(delivered_accounts) == qty:
                    break

        if len(delivered_accounts) < qty:
            await query.edit_message_text(text="❌ No enough available accounts to deliver.")
            return

        # Deliver to user
        await context.bot.send_message(
            user_id, 
            text=f"Your {qty} x {product} delivery:\n\n" + "\n\n".join(delivered_accounts)
        )

        # Update account statuses
        for index in row_indices:
            ACCOUNTS_SHEET.update_cell(index, 5, 'Assigned')  # Column E: Status
            ACCOUNTS_SHEET.update_cell(index, 6, user_id)     # Column F: Assigned To

        # Decrease stock in Products (Column D: Stock)
        prod_data = PRODUCTS_SHEET.get_all_records()
        for j, p in enumerate(prod_data, start=2):
            if p['Product Name'].lower() == product.lower():
                new_stock = int(p['Stock']) - qty
                PRODUCTS_SHEET.update_cell(j, 4, new_stock)
                break

        # Update Payments Sheet to approved (Column E: Status, assuming columns UserID, Product, Quantity, Proof, Status)
        PAYMENTS_SHEET.update_cell(payment_row_index, 5, 'approved')

        # Update the admin's message based on whether it was a photo or text
        message_id = context.user_data.get('admin_message_id')
        is_photo = context.user_data.get('is_photo', False)
        if message_id:
            if is_photo:
                await context.bot.edit_message_caption(
                    chat_id=ADMIN_ID,
                    message_id=message_id,
                    caption=f"✅ Approved and delivered for {qty} x {product} to user {user_id}.",
                    reply_markup=None
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=ADMIN_ID,
                    message_id=message_id,
                    text=f"✅ Approved and delivered for {qty} x {product} to user {user_id}.",
                    reply_markup=None
                )
        else:
            await query.edit_message_text(
                text=f"✅ Approved and delivered for {qty} x {product} to user {user_id}.",
                reply_markup=None
            )

        # Clear the stored data
        context.user_data.pop('admin_message_id', None)
        context.user_data.pop('is_photo', None)

    # Reject
    elif query.data.startswith("reject_"):
        parts = query.data.split("_")
        action = parts[0]
        user_id = int(parts[1])
        product = "_".join(parts[2:])

        # Update Payments Sheet to rejected
        pay_data = PAYMENTS_SHEET.get_all_records()
        for k, pay in enumerate(pay_data, start=2):
            if pay['UserID'] == user_id and pay['Product'] == product and pay['Status'] == 'pending':
                PAYMENTS_SHEET.update_cell(k, 5, 'rejected')  # Assuming Column E: Status
                break

        await context.bot.send_message(user_id, text=f"❌ Your payment for {product} was rejected.")

        # Update the admin's message based on whether it was a photo or text
        message_id = context.user_data.get('admin_message_id')
        is_photo = context.user_data.get('is_photo', False)
        if message_id:
            if is_photo:
                await context.bot.edit_message_caption(
                    chat_id=ADMIN_ID,
                    message_id=message_id,
                    caption=f"❌ Rejected for {product} from user {user_id}.",
                    reply_markup=None
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=ADMIN_ID,
                    message_id=message_id,
                    text=f"❌ Rejected for {product} from user {user_id}.",
                    reply_markup=None
                )
        else:
            await query.edit_message_text(
                text=f"❌ Rejected for {product} from user {user_id}.",
                reply_markup=None
            )

        # Clear the stored data
        context.user_data.pop('admin_message_id', None)
        context.user_data.pop('is_photo', None)


# ---------------- MAIN ---------------- #

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))   # Debugging ke liye
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), handle_message))

    print("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()