export interface Product {
  product_id: number
  name: string
  category: string
  description: string
  price_mmk: number
  credential_format: string
  active: number
  discount_pct: number
  discount_until: string | null
  fulfillment: string
  reseller_price_mmk: number | null
  emoji: string
  emoji_id: string
}

export interface User {
  telegram_id: number
  username: string
  mmk_balance: number
  total_spent: number
  notify_opt_out: number
  registered_at: string
  bonus_earned: number
  is_reseller: number
}

export interface Order {
  order_id: number
  telegram_id: number
  username: string
  product_id: number
  product_name: string
  amount_mmk: number
  payment_method: string
  credential: string
  status: string
  created_at: string
  fulfillment_mode: string
}

export interface Deposit {
  deposit_id: number
  telegram_id: number
  username: string
  method: string
  amount_mmk: number
  status: string
  created_at: string
  note: string | null
}

export interface Config {
  shop_name: string
  shop_tagline: string
  shop_subtagline: string
  welcome_message: string
  [key: string]: string
}

export interface Giveaway {
  giveaway_id: number
  product_id: number
  title: string
  description: string
  winner_slots: number
  end_time: string | null
  status: string
  created_at: string
}

export interface LdRound {
  round_id: number
  product_id: number
  product_name: string
  prize_mode: string
  prize_value: number
  entry_price: number
  total_slots: number
  deadline: string | null
  status: string
  seed_hash: string
  winner_id: number | null
  winner_slot: number | null
}

export interface StockCount {
  product_id: number
  count: number
}
