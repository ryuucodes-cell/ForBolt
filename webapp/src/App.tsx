import { useState, useEffect, useCallback } from 'react'
import { supabase } from './supabase'
import type { Product, Config, Order, Deposit, Giveaway, LdRound } from './types'

type Tab = 'shop' | 'wallet' | 'orders' | 'deposits' | 'giveaway' | 'lucky'

// ── Telegram WebApp helpers ─────────────────────────────────────
declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData: string
        initDataUnsafe: { user?: { id: number; username?: string; first_name?: string } }
        ready: () => void
        expand: () => void
        themeParams: { bg_color?: string; text_color?: string }
        BackButton: { show: () => void; hide: () => void; onClick: (cb: () => void) => void }
        MainButton: { setText: (t: string) => void; show: () => void; hide: () => void }
        close: () => void
        colorScheme: string
      }
    }
  }
}

function tgUser(): { id: number; username: string } {
  const u = window.Telegram?.WebApp?.initDataUnsafe?.user
  if (u) return { id: u.id, username: u.username || '' }
  // fallback for dev/testing without Telegram
  return { id: 12345, username: 'devuser' }
}

function fmtMMK(amount: number): string {
  return `${(amount || 0).toLocaleString()} MMK`
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' })
  } catch {
    return s.slice(0, 10)
  }
}

// ── Main App ────────────────────────────────────────────────────
export default function App() {
  const [tg] = useState(tgUser())
  const [tab, setTab] = useState<Tab>('shop')
  const [config, setConfig] = useState<Config | null>(null)
  const [balance, setBalance] = useState(0)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' | '' }>({ msg: '', type: '' })
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    window.Telegram?.WebApp?.ready()
    window.Telegram?.WebApp?.expand()
  }, [])

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast({ msg: '', type: '' }), 2500)
  }

  const loadConfig = useCallback(async () => {
    const { data, error } = await supabase.from('config').select('key, value')
    if (error || !data) return
    const cfg: Config = {} as Config
    for (const row of data) cfg[row.key] = row.value
    setConfig(cfg)
  }, [])

  const loadBalance = useCallback(async () => {
    const { data } = await supabase
      .from('users')
      .select('mmk_balance')
      .eq('telegram_id', tg.id)
      .maybeSingle()
    setBalance(data?.mmk_balance || 0)
  }, [tg.id])

  useEffect(() => {
    (async () => {
      await Promise.all([loadConfig(), loadBalance()])
      setLoading(false)
    })()
  }, [loadConfig, loadBalance])

  if (loading) {
    return <div className="loading">Loading shop...</div>
  }

  return (
    <>
      {toast.msg && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
      <Header config={config} balance={balance} onWallet={() => setTab('wallet')} />
      <Nav tab={tab} setTab={setTab} />
      {tab === 'shop' && <ShopTab tg={tg} config={config} onBalanceChange={loadBalance} showToast={showToast} />}
      {tab === 'wallet' && <WalletTab tg={tg} balance={balance} onBalanceChange={loadBalance} showToast={showToast} />}
      {tab === 'orders' && <OrdersTab tg={tg} showToast={showToast} />}
      {tab === 'deposits' && <DepositsTab tg={tg} showToast={showToast} />}
      {tab === 'giveaway' && <GiveawayTab tg={tg} showToast={showToast} />}
      {tab === 'lucky' && <LuckyDrawTab tg={tg} onBalanceChange={loadBalance} showToast={showToast} />}
    </>
  )
}

// ── Header ──────────────────────────────────────────────────────
function Header({ config, balance, onWallet }: { config: Config | null; balance: number; onWallet: () => void }) {
  return (
    <div className="header">
      <h1>{config?.shop_name || 'Shop'}</h1>
      <div className="tagline">{config?.shop_tagline || ''}</div>
      <div className="header-balance" onClick={onWallet}>
        <span className="bal-icon">💰</span>
        <span className="bal-amount">{fmtMMK(balance)}</span>
        <span className="bal-label">Balance</span>
      </div>
    </div>
  )
}

// ── Navigation ──────────────────────────────────────────────────
function Nav({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string }[] = [
    { id: 'shop', label: 'Shop' },
    { id: 'wallet', label: 'Wallet' },
    { id: 'orders', label: 'Orders' },
    { id: 'deposits', label: 'Deposits' },
    { id: 'giveaway', label: 'Giveaway' },
    { id: 'lucky', label: 'Lucky Draw' },
  ]
  return (
    <div className="nav">
      {tabs.map(t => (
        <button
          key={t.id}
          className={tab === t.id ? 'active' : ''}
          onClick={() => setTab(t.id)}
        >{t.label}</button>
      ))}
    </div>
  )
}

// ── Shop Tab ────────────────────────────────────────────────────
function ShopTab({ tg, config, onBalanceChange, showToast }: {
  tg: { id: number; username: string }
  config: Config | null
  onBalanceChange: () => void
  showToast: (msg: string, type?: 'success' | 'error') => void
}) {
  const [products, setProducts] = useState<Product[]>([])
  const [stockMap, setStockMap] = useState<Record<number, number>>({})
  const [category, setCategory] = useState('')
  const [categories, setCategories] = useState<string[]>([])
  const [selected, setSelected] = useState<Product | null>(null)
  const [loading, setLoading] = useState(true)

  const loadProducts = useCallback(async () => {
    const { data, error } = await supabase
      .from('products')
      .select('*')
      .eq('active', 1)
      .order('name', { ascending: true })

    if (error || !data) {
      showToast('Failed to load products', 'error')
      return
    }

    const prods = data as Product[]
    setProducts(prods)

    // load stock counts
    const { data: stockData } = await supabase
      .from('stock')
      .select('product_id')
      .is('reserved_for', null)

    const counts: Record<number, number> = {}
    if (stockData) {
      for (const row of stockData) {
        counts[row.product_id] = (counts[row.product_id] || 0) + 1
      }
    }
    setStockMap(counts)

    const cats = [...new Set(prods.map(p => p.category).filter(Boolean))]
    setCategories(cats)

    setLoading(false)
  }, [showToast])

  useEffect(() => {
    loadProducts()
  }, [loadProducts])

  if (loading) return <div className="loading">Loading products...</div>

  if (selected) {
    return (
      <ProductDetail
        product={selected}
        stock={stockMap[selected.product_id] || 0}
        tg={tg}
        onBack={() => setSelected(null)}
        onBalanceChange={onBalanceChange}
        showToast={showToast}
      />
    )
  }

  const filtered = category ? products.filter(p => p.category === category) : products
  const threshold = parseInt(config?.low_stock_threshold || '3')

  return (
    <>
      {categories.length > 1 && (
        <div className="categories">
          <button className={`cat-chip ${!category ? 'active' : ''}`} onClick={() => setCategory('')}>All</button>
          {categories.map(cat => (
            <button
              key={cat}
              className={`cat-chip ${category === cat ? 'active' : ''}`}
              onClick={() => setCategory(cat)}
            >{cat}</button>
          ))}
        </div>
      )}
      {filtered.length === 0 ? (
        <div className="empty">
          <div className="empty-icon">📦</div>
          <div className="empty-text">No products available yet.</div>
        </div>
      ) : (
        <div className="product-grid">
          {filtered.map(p => {
            const stock = stockMap[p.product_id] || 0
            const hasDiscount = p.discount_pct > 0
            const discountedPrice = hasDiscount
              ? Math.max(1, Math.floor(p.price_mmk * (100 - p.discount_pct) / 100))
              : p.price_mmk
            return (
              <div key={p.product_id} className="product-card" onClick={() => setSelected(p)}>
                <div className={`pc-stock ${stock === 0 ? 'out' : stock <= threshold ? 'low' : 'in'}`}>
                  {stock === 0 ? 'Out' : `${stock} left`}
                </div>
                <div className="pc-emoji">{p.emoji || '📦'}</div>
                <div className="pc-name">{p.name}</div>
                <div>
                  <span className="pc-price">{fmtMMK(discountedPrice)}</span>
                  {hasDiscount && <span className="pc-price-old">{fmtMMK(p.price_mmk)}</span>}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}

// ── Product Detail ──────────────────────────────────────────────
function ProductDetail({ product, stock, tg, onBack, onBalanceChange, showToast }: {
  product: Product
  stock: number
  tg: { id: number; username: string }
  onBack: () => void
  onBalanceChange: () => void
  showToast: (msg: string, type?: 'success' | 'error') => void
}) {
  const [buying, setBuying] = useState(false)
  const [qty, setQty] = useState(1)

  const hasDiscount = product.discount_pct > 0
  const discountedPrice = hasDiscount
    ? Math.max(1, Math.floor(product.price_mmk * (100 - product.discount_pct) / 100))
    : product.price_mmk
  const total = discountedPrice * qty
  const isPreorder = product.fulfillment === 'preorder'
  const lowThreshold = 3

  const handleBuy = async () => {
    if (stock === 0 && !isPreorder) {
      showToast('Out of stock', 'error')
      return
    }
    setBuying(true)

    // Ensure user exists
    await supabase
      .from('users')
      .upsert({ telegram_id: tg.id, username: tg.username })

    if (isPreorder) {
      // Create pre-order via RPC-like approach: deduct balance + create order
      const { data: user } = await supabase
        .from('users')
        .select('mmk_balance')
        .eq('telegram_id', tg.id)
        .maybeSingle()

      if (!user || user.mmk_balance < total) {
        showToast('Insufficient balance — please top up your wallet', 'error')
        setBuying(false)
        return
      }

      const { error: orderErr } = await supabase
        .from('orders')
        .insert({
          telegram_id: tg.id,
          username: tg.username,
          product_id: product.product_id,
          product_name: product.name,
          amount_mmk: total,
          payment_method: 'balance',
          credential: '',
          status: 'awaiting_fulfillment',
          fulfillment_mode: 'preorder',
        })

      if (orderErr) {
        showToast('Order failed — please try again', 'error')
        setBuying(false)
        return
      }

      await supabase
        .from('users')
        .update({ mmk_balance: user.mmk_balance - total, total_spent: 0 })
        .eq('telegram_id', tg.id)

      showToast('Pre-order placed! Admin will fulfill it soon.')
    } else {
      // Instant purchase
      const { data: user } = await supabase
        .from('users')
        .select('mmk_balance')
        .eq('telegram_id', tg.id)
        .maybeSingle()

      if (!user || user.mmk_balance < total) {
        showToast('Insufficient balance — please top up your wallet', 'error')
        setBuying(false)
        return
      }

      // Claim stock items
      const { data: stockItems } = await supabase
        .from('stock')
        .select('stock_id, credential')
        .eq('product_id', product.product_id)
        .is('reserved_for', null)
        .limit(qty)

      if (!stockItems || stockItems.length < qty) {
        showToast('Not enough stock available', 'error')
        setBuying(false)
        return
      }

      // Delete claimed stock
      const stockIds = stockItems.map(s => s.stock_id)
      await supabase.from('stock').delete().in('stock_id', stockIds)

      // Create orders
      for (const item of stockItems) {
        await supabase.from('orders').insert({
          telegram_id: tg.id,
          username: tg.username,
          product_id: product.product_id,
          product_name: product.name,
          amount_mmk: discountedPrice,
          payment_method: 'balance',
          credential: item.credential,
          status: 'completed',
        })
      }

      // Deduct balance
      await supabase
        .from('users')
        .update({ mmk_balance: user.mmk_balance - total })
        .eq('telegram_id', tg.id)

      showToast(`Purchase successful! ${qty} item(s) delivered.`)
    }

    onBalanceChange()
    setBuying(false)
    onBack()
  }

  return (
    <>
      <div className="back-bar">
        <button onClick={onBack}>← Back to Shop</button>
      </div>
      <div className="detail">
        <div className="d-emoji">{product.emoji || '📦'}</div>
        <h2>{product.name}</h2>
        <div>
          <span className="d-price">{fmtMMK(discountedPrice)}</span>
          {hasDiscount && <span className="d-price-old">{fmtMMK(product.price_mmk)}</span>}
        </div>
        {product.description && <div className="d-desc">{product.description}</div>}
        {!isPreorder && (
          <div className={`d-stock ${stock === 0 ? 'out' : stock <= lowThreshold ? 'low' : 'in'}`}>
            {stock === 0 ? '✗ Out of stock' : stock <= lowThreshold ? `⚠ Only ${stock} left` : `✓ ${stock} in stock`}
          </div>
        )}
        {isPreorder && (
          <div className="d-stock in" style={{ background: 'rgba(59,130,246,0.15)', color: 'var(--primary)' }}>
            📋 Pre-order — fulfilled after purchase
          </div>
        )}

        {!isPreorder && stock > 0 && (
          <>
            <div className="section-title" style={{ padding: '0' }}>Quantity</div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8, marginBottom: 16 }}>
              {[1, 2, 3, 5].map(n => (
                <button
                  key={n}
                  className={`cat-chip ${qty === n ? 'active' : ''}`}
                  onClick={() => setQty(n)}
                >{n}</button>
              ))}
            </div>
          </>
        )}

        <div style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 12 }}>
          Total: <span style={{ color: 'var(--accent)', fontWeight: 700, fontSize: 18 }}>{fmtMMK(total)}</span>
        </div>

        <button
          className="btn btn-primary"
          disabled={buying || (!isPreorder && stock === 0)}
          onClick={handleBuy}
        >
          {buying ? 'Processing...' : isPreorder ? 'Place Pre-Order' : 'Buy Now'}
        </button>
      </div>
    </>
  )
}

// ── Wallet Tab ──────────────────────────────────────────────────
function WalletTab({ tg, balance, onBalanceChange, showToast }: {
  tg: { id: number; username: string }
  balance: number
  onBalanceChange: () => void
  showToast: (msg: string, type?: 'success' | 'error') => void
}) {
  const [user, setUser] = useState<{ total_spent: number; bonus_earned: number; is_reseller: number } | null>(null)

  useEffect(() => {
    (async () => {
      const { data } = await supabase
        .from('users')
        .select('total_spent, bonus_earned, is_reseller')
        .eq('telegram_id', tg.id)
        .maybeSingle()
      setUser(data as any)
    })()
  }, [tg.id])

  return (
    <>
      <div className="wallet-card">
        <div className="wc-label">Wallet Balance</div>
        <div className="wc-balance">{fmtMMK(balance)}</div>
        <div className="wc-stats">
          <div className="wc-stat">
            <div className="wc-stat-label">Total Spent</div>
            <div className="wc-stat-value">{fmtMMK(user?.total_spent || 0)}</div>
          </div>
          <div className="wc-stat">
            <div className="wc-stat-label">Bonus Earned</div>
            <div className="wc-stat-value">{fmtMMK(user?.bonus_earned || 0)}</div>
          </div>
        </div>
      </div>

      <div className="section-title">Account</div>
      <div className="profile-card">
        <div className="profile-row">
          <span className="pr-label">Telegram ID</span>
          <span className="pr-value">{tg.id}</span>
        </div>
        <div className="profile-row">
          <span className="pr-label">Username</span>
          <span className="pr-value">@{tg.username || '—'}</span>
        </div>
        <div className="profile-row">
          <span className="pr-label">Role</span>
          <span className="pr-value">{user?.is_reseller ? 'Reseller' : 'Customer'}</span>
        </div>
      </div>

      <div className="section-title">Top Up</div>
      <div style={{ padding: '0 12px' }}>
        <div style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 12 }}>
          To add balance, send a payment via your preferred method and submit a deposit through the Telegram bot. The admin will approve it and your balance will update here automatically.
        </div>
        <button className="btn btn-secondary" onClick={() => {
          window.Telegram?.WebApp?.close?.()
          window.open('tg://user?id=' + tg.id, '_blank')
        }}>
          Open Telegram Bot
        </button>
      </div>
    </>
  )
}

// ── Orders Tab ──────────────────────────────────────────────────
function OrdersTab({ tg, showToast }: {
  tg: { id: number; username: string }
  showToast: (msg: string, type?: 'success' | 'error') => void
}) {
  const [orders, setOrders] = useState<Order[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    const { data } = await supabase
      .from('orders')
      .select('*')
      .eq('telegram_id', tg.id)
      .order('created_at', { ascending: false })
      .limit(50)
    setOrders((data || []) as Order[])
    setLoading(false)
  }, [tg.id])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="loading">Loading orders...</div>

  if (orders.length === 0) {
    return (
      <div className="empty">
        <div className="empty-icon">🧾</div>
        <div className="empty-text">No purchases yet. Browse the shop to get started!</div>
      </div>
    )
  }

  return (
    <div className="list">
      {orders.map(o => (
        <div key={o.order_id} className="list-item">
          <div className="li-title">{o.product_name}</div>
          <div className="li-row">
            <span className="li-sub">{fmtDate(o.created_at)}</span>
            <span className="li-sub" style={{ fontWeight: 700, color: 'var(--accent)' }}>{fmtMMK(o.amount_mmk)}</span>
          </div>
          <div className="li-row" style={{ marginTop: 6 }}>
            <span className={`badge ${
              o.status === 'completed' ? 'badge-success' :
              o.status === 'refunded' ? 'badge-error' :
              o.status === 'awaiting_fulfillment' ? 'badge-warning' :
              'badge-info'
            }`}>{o.status.replace(/_/g, ' ')}</span>
            <span className="li-sub">#{o.order_id}</span>
          </div>
          {o.credential && o.status === 'completed' && (
            <div style={{
              marginTop: 10,
              padding: 10,
              background: 'var(--surface-2)',
              borderRadius: 8,
              fontSize: 13,
              fontFamily: 'monospace',
              wordBreak: 'break-all',
              color: 'var(--text-secondary)',
            }}>
              {o.credential}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Deposits Tab ────────────────────────────────────────────────
function DepositsTab({ tg, showToast }: {
  tg: { id: number; username: string }
  showToast: (msg: string, type?: 'success' | 'error') => void
}) {
  const [deposits, setDeposits] = useState<Deposit[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    (async () => {
      const { data } = await supabase
        .from('deposits')
        .select('*')
        .eq('telegram_id', tg.id)
        .order('created_at', { ascending: false })
        .limit(50)
      setDeposits((data || []) as Deposit[])
      setLoading(false)
    })()
  }, [tg.id])

  if (loading) return <div className="loading">Loading deposits...</div>

  if (deposits.length === 0) {
    return (
      <div className="empty">
        <div className="empty-icon">📋</div>
        <div className="empty-text">No deposits yet. Top up via the Telegram bot!</div>
      </div>
    )
  }

  return (
    <div className="list">
      {deposits.map(d => (
        <div key={d.deposit_id} className="list-item">
          <div className="li-row">
            <span className="li-title">{fmtMMK(d.amount_mmk)}</span>
            <span className={`badge ${
              d.status === 'approved' ? 'badge-success' :
              d.status === 'rejected' ? 'badge-error' :
              'badge-warning'
            }`}>{d.status}</span>
          </div>
          <div className="li-row" style={{ marginTop: 6 }}>
            <span className="li-sub">{d.method.toUpperCase()}</span>
            <span className="li-sub">{fmtDate(d.created_at)}</span>
          </div>
          {d.note && d.status === 'rejected' && (
            <div className="li-sub" style={{ marginTop: 6, color: 'var(--error)' }}>
              Reason: {d.note}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Giveaway Tab ────────────────────────────────────────────────
function GiveawayTab({ tg, showToast }: {
  tg: { id: number; username: string }
  showToast: (msg: string, type?: 'success' | 'error') => void
}) {
  const [giveaways, setGiveaways] = useState<Giveaway[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    (async () => {
      const { data } = await supabase
        .from('giveaways')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(20)
      setGiveaways((data || []) as Giveaway[])
      setLoading(false)
    })()
  }, [])

  if (loading) return <div className="loading">Loading giveaways...</div>

  if (giveaways.length === 0) {
    return (
      <div className="empty">
        <div className="empty-icon">🎁</div>
        <div className="empty-text">No giveaways available right now.</div>
      </div>
    )
  }

  return (
    <>
      {giveaways.map(gw => (
        <div key={gw.giveaway_id} className="giveaway-card">
          <div className="gw-title">{gw.title}</div>
          {gw.description && <div className="gw-desc">{gw.description}</div>}
          <div className="gw-meta">
            <div className="gw-meta-item">
              <div className="gw-meta-val">{gw.winner_slots}</div>
              <div className="gw-meta-label">Winners</div>
            </div>
            <div className="gw-meta-item">
              <div className="gw-meta-val">{fmtDate(gw.end_time)}</div>
              <div className="gw-meta-label">Ends</div>
            </div>
          </div>
          <span className={`badge ${
            gw.status === 'active' ? 'badge-success' :
            gw.status === 'drawn' ? 'badge-info' :
            'badge-muted'
          }`}>{gw.status}</span>
        </div>
      ))}
    </>
  )
}

// ── Lucky Draw Tab ──────────────────────────────────────────────
function LuckyDrawTab({ tg, onBalanceChange, showToast }: {
  tg: { id: number; username: string }
  onBalanceChange: () => void
  showToast: (msg: string, type?: 'success' | 'error') => void
}) {
  const [rounds, setRounds] = useState<LdRound[]>([])
  const [loading, setLoading] = useState(true)
  const [buying, setBuying] = useState<number | null>(null)

  const load = useCallback(async () => {
    const { data } = await supabase
      .from('ld_rounds')
      .select('*')
      .order('round_id', { ascending: false })
      .limit(20)
    setRounds((data || []) as LdRound[])
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const handleBuySlot = async (round: LdRound) => {
    setBuying(round.round_id)

    const { data: user } = await supabase
      .from('users')
      .select('mmk_balance')
      .eq('telegram_id', tg.id)
      .maybeSingle()

    if (!user || user.mmk_balance < round.entry_price) {
      showToast('Insufficient balance', 'error')
      setBuying(null)
      return
    }

    // Count current entries
    const { count } = await supabase
      .from('ld_entries')
      .select('*', { count: 'exact', head: true })
      .eq('round_id', round.round_id)

    if (count !== null && count >= round.total_slots) {
      showToast('All slots are full', 'error')
      setBuying(null)
      return
    }

    const slotNo = (count || 0) + 1

    // Insert entry
    const { error } = await supabase
      .from('ld_entries')
      .insert({
        round_id: round.round_id,
        telegram_id: tg.id,
        username: tg.username,
        slot_no: slotNo,
      })

    if (error) {
      showToast('Failed to buy slot', 'error')
      setBuying(null)
      return
    }

    // Deduct balance
    await supabase
      .from('users')
      .update({ mmk_balance: user.mmk_balance - round.entry_price })
      .eq('telegram_id', tg.id)

    showToast(`Slot #${slotNo} purchased!`)
    onBalanceChange()
    setBuying(null)
    load()
  }

  if (loading) return <div className="loading">Loading lucky draws...</div>

  if (rounds.length === 0) {
    return (
      <div className="empty">
        <div className="empty-icon">🎲</div>
        <div className="empty-text">No lucky draw rounds right now.</div>
      </div>
    )
  }

  return (
    <>
      {rounds.map(r => (
        <div key={r.round_id} className="ld-card">
          <div className="ld-title">{r.product_name}</div>
          <div className="li-sub">Entry: <span style={{ color: 'var(--accent)', fontWeight: 700 }}>{fmtMMK(r.entry_price)}</span></div>
          <div className="li-sub" style={{ marginTop: 4 }}>
            {r.total_slots} slots · Ends {fmtDate(r.deadline)}
          </div>

          {/* Slots visualization */}
          <LdSlots roundId={r.round_id} totalSlots={r.total_slots} tgId={tg.id} />

          <div className="li-row" style={{ marginTop: 6 }}>
            <span className={`badge ${
              r.status === 'open' ? 'badge-success' :
              r.status === 'completed' ? 'badge-info' :
              'badge-muted'
            }`}>{r.status}</span>
          </div>

          {r.status === 'open' && (
            <button
              className="btn btn-primary"
              style={{ marginTop: 12 }}
              disabled={buying === r.round_id}
              onClick={() => handleBuySlot(r)}
            >
              {buying === r.round_id ? 'Processing...' : `Buy Slot — ${fmtMMK(r.entry_price)}`}
            </button>
          )}
        </div>
      ))}
    </>
  )
}

function LdSlots({ roundId, totalSlots, tgId }: { roundId: number; totalSlots: number; tgId: number }) {
  const [entries, setEntries] = useState<{ slot_no: number; telegram_id: number }[]>([])

  useEffect(() => {
    (async () => {
      const { data } = await supabase
        .from('ld_entries')
        .select('slot_no, telegram_id')
        .eq('round_id', roundId)
        .order('slot_no')
      setEntries(data || [])
    })()
  }, [roundId])

  const filledMap = new Map(entries.map(e => [e.slot_no, e.telegram_id]))
  const displaySlots = Math.min(totalSlots, 50)

  return (
    <div className="ld-slots">
      {Array.from({ length: displaySlots }, (_, i) => {
        const slotNo = i + 1
        const filledBy = filledMap.get(slotNo)
        const isMine = filledBy === tgId
        return (
          <div
            key={slotNo}
            className={`ld-slot ${filledBy ? 'filled' : ''} ${isMine ? 'mine' : ''}`}
          >{slotNo}</div>
        )
      })}
    </div>
  )
}
