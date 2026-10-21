(() => {
  'use strict';

  const STORAGE_KEY = 'personal-ledger-currency-v1';
  const DEFAULT_OPENING_BYN = 100;
  const EPSILON = 0.0000001;
  const moneyFormatter = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const rateFormatter = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 6 });
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    headerByn: $('#header-byn'),
    headerBynLabel: $('#header-byn-label'),
    headerUsd: $('#header-usd'),
    headerUsdLabel: $('#header-usd-label'),
    summaryUsdLabel: $('#summary-usd-label'),
    tradeForm: $('#trade-form'),
    date: $('#operation-date'),
    owner: $('#owner-select'),
    operation: $('#operation-select'),
    amount: $('#amount-input'),
    amountCurrency: $('#input-currency'),
    amountLabel: $('#amount-label'),
    amountHint: $('#amount-hint'),
    rate: $('#rate-input'),
    rateLabel: $('#rate-label'),
    rateHint: $('#rate-hint'),
    submit: $('#submit-trade'),
    previewCaption: $('#preview-caption'),
    previewValue: $('#preview-value'),
    previewResult: $('#preview-result'),
    feedback: $('#trade-feedback'),
    operationHint: $('#operation-hint'),
    summaryUsd: $('#summary-usd'),
    summaryCost: $('#summary-cost'),
    summaryPnl: $('#summary-pnl'),
    summaryPnlLabel: $('#summary-pnl-label'),
    summaryPnlItem: $('#summary-pnl').parentElement,
    historyBody: $('#history-body'),
    emptyHistory: $('#empty-history'),
    clearHistory: $('#clear-history'),
    openOpeningDialog: $('#open-opening-dialog'),
    openingDialog: $('#opening-dialog'),
    openingAmount: $('#opening-amount-input'),
    openingFeedback: $('#opening-feedback'),
    closeOpeningDialog: $('#close-opening-dialog'),
    cancelOpeningDialog: $('#cancel-opening-dialog'),
    saveOpeningDialog: $('#save-opening-dialog'),
    helpButton: $('#help-button'),
    helpDialog: $('#help-dialog'),
    closeHelpDialog: $('#close-help-dialog'),
    closeHelpButton: $('#close-help-button'),
    exportCsv: $('#export-csv'),
  };

  let state = loadState();
  let operation = 'buy';
  let inputUnit = 'BYN';

  function roundMoney(value) {
    if (!Number.isFinite(value)) return NaN;
    const rounded = Math.round((value + Number.EPSILON) * 100) / 100;
    return Number.isFinite(rounded) ? rounded : NaN;
  }

  function parseDecimal(value) {
    const normalized = String(value ?? '').trim().replace(/\s+/g, '').replace(',', '.');
    if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) return NaN;
    const number = Number(normalized);
    return Number.isFinite(number) ? number : NaN;
  }

  function formatNumber(value) {
    return moneyFormatter.format(Number.isFinite(value) ? value : 0);
  }

  function formatByn(value) {
    return `${formatNumber(value)} Br`;
  }

  function formatUsd(value) {
    return `${formatNumber(value)} $`;
  }

  function formatRate(value) {
    return rateFormatter.format(value);
  }

  function formatSignedByn(value, showPlus = true) {
    if (!Number.isFinite(value) || Math.abs(value) < 0.005) return formatByn(0);
    const sign = value > 0 ? (showPlus ? '+' : '') : '−';
    return `${sign}${formatByn(Math.abs(value))}`;
  }

  function localDateValue(date = new Date()) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function createId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function safeTransaction(tx) {
    if (!tx || (tx.type !== 'buy' && tx.type !== 'sell')) return null;
    const keys = tx.type === 'buy'
      ? ['bynAmount', 'usdAmount', 'rate']
      : ['usdAmount', 'rate', 'proceedsByn', 'costByn'];
    for (const key of keys) {
      if (!Number.isFinite(Number(tx[key])) || Number(tx[key]) < 0) return null;
    }
    if (tx.type === 'sell' && !Number.isFinite(Number(tx.pnlByn))) return null;
    const date = String(tx.date || '');
    if (!date || Number(tx.rate) <= 0) return null;
    return {
      id: String(tx.id || createId()),
      type: tx.type,
      date,
      owner: String(tx.owner || 'Юра'),
      rate: Number(tx.rate),
      ...(tx.type === 'buy'
        ? { bynAmount: Number(tx.bynAmount), usdAmount: Number(tx.usdAmount) }
        : {
            usdAmount: Number(tx.usdAmount),
            proceedsByn: Number(tx.proceedsByn),
            costByn: Number(tx.costByn),
            pnlByn: Number(tx.pnlByn),
          }),
    };
  }

  function defaultState() {
    return { openingBynByOwner: { Юра: DEFAULT_OPENING_BYN, Игорь: 0 }, transactions: [] };
  }

  function loadState() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (!saved) return defaultState();
      const parsed = JSON.parse(saved);
      if (!Array.isArray(parsed?.transactions)) return defaultState();

      const openingBynByOwner = {};
      if (parsed.openingBynByOwner && typeof parsed.openingBynByOwner === 'object') {
        for (const [owner, amount] of Object.entries(parsed.openingBynByOwner)) {
          const value = Number(amount);
          const rounded = Number.isFinite(value) && value >= 0 ? roundMoney(value) : NaN;
          if (Number.isFinite(rounded)) openingBynByOwner[owner] = rounded;
        }
      }
      if (Object.keys(openingBynByOwner).length === 0) {
        const legacyAmount = Number(parsed.openingByn ?? DEFAULT_OPENING_BYN);
        const roundedLegacy = Number.isFinite(legacyAmount) && legacyAmount >= 0 ? roundMoney(legacyAmount) : DEFAULT_OPENING_BYN;
        openingBynByOwner.Юра = Number.isFinite(roundedLegacy) ? roundedLegacy : DEFAULT_OPENING_BYN;
        openingBynByOwner.Игорь = 0;
      }
      return {
        openingBynByOwner,
        transactions: parsed.transactions.map(safeTransaction).filter(Boolean),
      };
    } catch (_error) {
      return defaultState();
    }
  }

  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function calculateLedger(owner = elements.owner.value || 'Юра') {
    let byn = Number(state.openingBynByOwner[owner] || 0);
    let usd = 0;
    let usdCostByn = 0;
    let realizedPnl = 0;

    for (const tx of state.transactions) {
      if ((tx.owner || 'Юра') !== owner) continue;
      if (tx.type === 'buy') {
        byn -= tx.bynAmount;
        usd += tx.usdAmount;
        usdCostByn += tx.bynAmount;
      } else {
        byn += tx.proceedsByn;
        usd -= tx.usdAmount;
        usdCostByn -= tx.costByn;
        realizedPnl += tx.pnlByn;
      }
    }

    if (Math.abs(byn) < EPSILON) byn = 0;
    if (Math.abs(usd) < EPSILON) {
      usd = 0;
      usdCostByn = 0;
    }
    if (Math.abs(usdCostByn) < EPSILON) usdCostByn = 0;
    return {
      byn,
      usd,
      usdCostByn,
      averageCostByn: usd > EPSILON ? usdCostByn / usd : 0,
      realizedPnl,
    };
  }

  function setFeedback(message = '', kind = 'error') {
    elements.feedback.textContent = message;
    elements.feedback.classList.toggle('is-error', Boolean(message) && kind === 'error');
    elements.feedback.classList.toggle('is-success', Boolean(message) && kind === 'success');
  }

  function renderSummary() {
    const owner = elements.owner.value || 'Юра';
    const ledger = calculateLedger(owner);
    elements.headerBynLabel.textContent = `BYN-счёт · ${owner}`;
    elements.headerUsdLabel.textContent = `Остаток USD · ${owner}`;
    elements.summaryUsdLabel.textContent = `Остаток · ${owner}`;
    elements.headerByn.textContent = formatByn(ledger.byn);
    elements.headerUsd.textContent = formatUsd(ledger.usd);
    elements.summaryUsd.textContent = formatUsd(ledger.usd);
    elements.summaryCost.textContent = formatByn(ledger.usdCostByn);
    elements.summaryPnl.textContent = formatSignedByn(ledger.realizedPnl, false);
    elements.summaryPnlItem.classList.toggle('is-positive', ledger.realizedPnl > 0.005);
    elements.summaryPnlItem.classList.toggle('is-negative', ledger.realizedPnl < -0.005);
    if (ledger.realizedPnl > 0.005) elements.summaryPnlLabel.textContent = 'Реализованная прибыль';
    else if (ledger.realizedPnl < -0.005) elements.summaryPnlLabel.textContent = 'Реализованная потеря';
    else elements.summaryPnlLabel.textContent = 'Реализованный результат';
  }

  function calculateOperation() {
    const ledger = calculateLedger();
    const rawAmount = parseDecimal(elements.amount.value);
    const amount = Number.isFinite(rawAmount) ? roundMoney(rawAmount) : NaN;
    const rate = parseDecimal(elements.rate.value);
    const unit = inputUnit;

    if (elements.amount.value.trim() === '') return { valid: false, blank: true, ledger };
    if (!Number.isFinite(amount) || amount <= 0) return { valid: false, error: `Введите сумму больше 0,01 ${unit}.`, ledger };
    if (!Number.isFinite(rate) || rate <= 0) return { valid: false, error: 'Укажите положительный курс банка.', ledger };

    if (operation === 'buy') {
      const bynAmount = unit === 'BYN' ? amount : roundMoney(amount * rate);
      const usdAmount = unit === 'USD' ? amount : roundMoney(amount / rate);
      if (!Number.isFinite(bynAmount) || !Number.isFinite(usdAmount) || bynAmount <= 0 || usdAmount <= 0) {
        return { valid: false, error: 'Проверьте сумму и курс банка.', ledger };
      }
      const data = { valid: bynAmount <= ledger.byn + EPSILON, bynAmount, usdAmount, rate, ledger };
      if (!data.valid) data.error = `Нужно ${formatByn(bynAmount)}, на BYN-счёте ${formatByn(ledger.byn)}.`;
      data.preview = `${formatByn(bynAmount)} → ${formatUsd(usdAmount)}`;
      data.resultText = `Покупка: ${formatUsd(usdAmount)} за ${formatByn(bynAmount)}`;
      return data;
    }

    const usdAmount = unit === 'USD' ? amount : roundMoney(amount / rate);
    const proceedsByn = unit === 'BYN' ? roundMoney(usdAmount * rate) : roundMoney(amount * rate);
    if (!Number.isFinite(usdAmount) || !Number.isFinite(proceedsByn) || usdAmount <= 0 || proceedsByn <= 0) {
      return { valid: false, error: 'Проверьте сумму и курс банка.', ledger };
    }
    if (ledger.usd <= EPSILON) return { valid: false, error: 'На валютном счёте пока нет USD. Сначала запишите покупку.', ledger };
    if (usdAmount > ledger.usd + EPSILON) {
      return { valid: false, error: `Нужно ${formatUsd(usdAmount)}, на валютном счёте ${formatUsd(ledger.usd)}.`, ledger };
    }
    const isSellingAll = Math.abs(usdAmount - ledger.usd) < 0.005;
    const costByn = isSellingAll ? ledger.usdCostByn : usdAmount * ledger.averageCostByn;
    const pnlByn = proceedsByn - costByn;
    return {
      valid: Number.isFinite(costByn) && Number.isFinite(pnlByn),
      usdAmount,
      proceedsByn,
      costByn,
      pnlByn,
      rate,
      ledger,
      error: Number.isFinite(costByn) && Number.isFinite(pnlByn) ? '' : 'Не удалось рассчитать себестоимость операции.',
      preview: `${formatUsd(usdAmount)} → ${formatByn(proceedsByn)}`,
      resultText: `${formatByn(proceedsByn)} · ${formatSignedByn(pnlByn)} к себестоимости`,
    };
  }

  function renderForm() {
    operation = elements.operation.value;
    const isBuy = operation === 'buy';
    const unit = inputUnit;
    elements.amountCurrency.value = inputUnit;
    const ledger = calculateLedger();
    const bynInput = unit === 'BYN';

    elements.amountLabel.textContent = `Сумма ${unit}`;
    elements.amountHint.textContent = isBuy
      ? (bynInput ? `Доступно: ${formatByn(ledger.byn)}` : `BYN к списанию: ${formatByn(ledger.byn)}`)
      : (bynInput ? `Остаток USD: ${formatUsd(ledger.usd)}` : `Доступно: ${formatUsd(ledger.usd)}`);
    elements.rateLabel.textContent = 'Курс банка BYN/USD';
    elements.rateHint.textContent = isBuy ? 'Покупка: курс продажи банка' : 'Продажа: курс покупки банка';
    elements.operationHint.textContent = isBuy
      ? 'Покупка: выберите сумму в BYN, чтобы рассчитать доллары, или в USD, чтобы рассчитать списание BYN. Применяется курс продажи банка.'
      : 'Продажа: выберите USD к продаже или сумму BYN к зачислению. Применяется курс покупки банка; результат считается по средней себестоимости USD.';

    const calculation = calculateOperation();
    elements.previewCaption.textContent = isBuy ? 'Расчёт покупки' : 'Расчёт продажи';
    elements.previewValue.textContent = calculation.preview || 'Введите сумму и курс банка';
    elements.previewResult.textContent = calculation.resultText || (isBuy ? 'Купленные USD попадут на валютный счёт' : 'Разница появится при продаже');
    elements.previewResult.classList.toggle('is-negative', Number.isFinite(calculation.pnlByn) && calculation.pnlByn < -0.005);
    elements.submit.disabled = !calculation.valid;
    setFeedback(calculation.error || '');
  }

  function element(tag, text, className = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderHistory() {
    const transactions = [...state.transactions].sort((a, b) => String(b.date).localeCompare(String(a.date)));
    elements.historyBody.replaceChildren();
    elements.emptyHistory.hidden = transactions.length > 0;
    elements.clearHistory.hidden = transactions.length === 0;

    for (const tx of transactions) {
      const row = document.createElement('tr');
      const date = element('td', tx.date.slice(0, 10));
      const owner = element('td', tx.owner || 'Юра');
      const operationCell = element('td', tx.type === 'buy' ? 'Покупка USD' : 'Продажа USD');
      const usd = element('td', `${tx.type === 'buy' ? '+' : '−'}${formatUsd(tx.usdAmount)}`);
      const rate = element('td', formatRate(tx.rate));
      const byn = element('td', formatByn(tx.type === 'buy' ? tx.bynAmount : tx.proceedsByn));
      const cost = element('td', formatByn(tx.type === 'buy' ? tx.bynAmount : tx.costByn));
      const result = element('td', tx.type === 'buy' ? '—' : formatSignedByn(tx.pnlByn));
      result.className = tx.type === 'buy' ? 'neutral-cell' : tx.pnlByn > 0.005 ? 'profit-cell' : tx.pnlByn < -0.005 ? 'loss-cell' : 'neutral-cell';
      const overall = element('td', 'Не добавлено');
      row.append(date, owner, operationCell, usd, rate, byn, cost, result, overall);
      elements.historyBody.append(row);
    }
  }

  function renderAll() {
    renderSummary();
    renderHistory();
    renderForm();
  }

  function convertAmountForUnit(nextUnit) {
    const currentUnit = inputUnit;
    if (currentUnit === nextUnit) return;
    const amount = parseDecimal(elements.amount.value);
    const rate = parseDecimal(elements.rate.value);
    if (Number.isFinite(amount) && amount > 0 && Number.isFinite(rate) && rate > 0) {
      const result = operation === 'buy'
        ? (currentUnit === 'BYN' ? amount / rate : amount * rate)
        : (currentUnit === 'USD' ? amount * rate : amount / rate);
      const converted = roundMoney(result);
      elements.amount.value = Number.isFinite(converted) && converted > 0 ? converted.toFixed(2) : '';
    }
    inputUnit = nextUnit;
    elements.amountCurrency.value = nextUnit;
    renderForm();
  }

  function configureOperation(nextOperation) {
    operation = nextOperation === 'sell' ? 'sell' : 'buy';
    elements.operation.value = operation;
    if (operation === 'buy') {
      inputUnit = 'BYN';
      elements.amountCurrency.value = 'BYN';
      elements.amount.value = '';
      elements.rate.value = '';
    } else {
      inputUnit = 'USD';
      elements.amountCurrency.value = 'USD';
      elements.amount.value = '';
      elements.rate.value = '';
    }
    renderForm();
  }

  function addOperation(event) {
    event.preventDefault();
    const calculation = calculateOperation();
    if (!calculation.valid) {
      setFeedback(calculation.error || 'Проверьте сумму и курс.');
      return;
    }
    const common = {
      id: createId(),
      type: operation,
      date: elements.date.value || localDateValue(),
      owner: elements.owner.value || 'Юра',
      rate: calculation.rate,
    };
    if (operation === 'buy') {
      state.transactions.push({
        ...common,
        bynAmount: calculation.bynAmount,
        usdAmount: calculation.usdAmount,
      });
    } else {
      state.transactions.push({
        ...common,
        usdAmount: calculation.usdAmount,
        proceedsByn: calculation.proceedsByn,
        costByn: calculation.costByn,
        pnlByn: calculation.pnlByn,
      });
    }
    saveState();
    renderSummary();
    renderHistory();
    elements.amount.value = '';
    renderForm();
    setFeedback(`${operation === 'buy' ? 'Покупка' : 'Продажа'} USD добавлена в журнал.`, 'success');
  }

  function openOpeningDialog() {
    const owner = elements.owner.value || 'Юра';
    const hasTransactions = state.transactions.some((tx) => (tx.owner || 'Юра') === owner);
    elements.openingAmount.value = Number(state.openingBynByOwner[owner] || 0).toFixed(2);
    elements.openingAmount.disabled = hasTransactions;
    elements.saveOpeningDialog.disabled = hasTransactions;
    elements.openingFeedback.textContent = hasTransactions
      ? 'После первой операции начальный остаток нельзя изменить. Очистите журнал, чтобы начать заново.'
      : '';
    if (typeof elements.openingDialog.showModal === 'function') elements.openingDialog.showModal();
    else elements.openingDialog.setAttribute('open', '');
  }

  function closeDialog(dialog) {
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  function saveOpening() {
    const owner = elements.owner.value || 'Юра';
    if (state.transactions.some((tx) => (tx.owner || 'Юра') === owner)) return;
    const value = parseDecimal(elements.openingAmount.value);
    const rounded = Number.isFinite(value) ? roundMoney(value) : NaN;
    if (!Number.isFinite(rounded) || rounded < 0) {
      elements.openingFeedback.textContent = 'Введите корректную сумму BYN (не меньше нуля).';
      return;
    }
    state.openingBynByOwner[owner] = rounded;
    saveState();
    renderAll();
    closeDialog(elements.openingDialog);
  }

  function exportCsv() {
    const rows = [
      ['Дата', 'Собственник', 'Операция', 'USD', 'Курс BYN/USD', 'BYN', 'Себестоимость BYN', 'Результат BYN'],
      ...state.transactions.map((tx) => [
        tx.date.slice(0, 10),
        tx.owner || 'Юра',
        tx.type === 'buy' ? 'Покупка USD' : 'Продажа USD',
        tx.usdAmount.toFixed(2),
        tx.rate,
        (tx.type === 'buy' ? tx.bynAmount : tx.proceedsByn).toFixed(2),
        (tx.type === 'buy' ? tx.bynAmount : tx.costByn).toFixed(2),
        tx.type === 'buy' ? '' : tx.pnlByn.toFixed(2),
      ]),
    ];
    const csv = `\uFEFF${rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(';')).join('\r\n')}`;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'currency-ledger.csv';
    link.click();
    URL.revokeObjectURL(url);
  }

  elements.tradeForm.addEventListener('submit', addOperation);
  elements.amount.addEventListener('input', renderForm);
  elements.rate.addEventListener('input', renderForm);
  elements.amountCurrency.addEventListener('change', () => convertAmountForUnit(elements.amountCurrency.value));
  elements.operation.addEventListener('change', () => configureOperation(elements.operation.value));
  elements.owner.addEventListener('change', renderAll);
  elements.openOpeningDialog.addEventListener('click', openOpeningDialog);
  elements.closeOpeningDialog.addEventListener('click', () => closeDialog(elements.openingDialog));
  elements.cancelOpeningDialog.addEventListener('click', () => closeDialog(elements.openingDialog));
  elements.saveOpeningDialog.addEventListener('click', saveOpening);
  elements.helpButton.addEventListener('click', () => {
    if (typeof elements.helpDialog.showModal === 'function') elements.helpDialog.showModal();
    else elements.helpDialog.setAttribute('open', '');
  });
  elements.closeHelpDialog.addEventListener('click', () => closeDialog(elements.helpDialog));
  elements.closeHelpButton.addEventListener('click', () => closeDialog(elements.helpDialog));
  elements.exportCsv.addEventListener('click', exportCsv);
  elements.clearHistory.addEventListener('click', () => {
    if (!window.confirm('Удалить все операции из журнала? Начальный остаток BYN сохранится.')) return;
    state.transactions = [];
    saveState();
    configureOperation('buy');
    renderAll();
  });

  elements.date.value = localDateValue();
  const initialLedger = calculateLedger();
  if (initialLedger.usd > EPSILON) configureOperation('sell');
  else configureOperation('buy');
  renderAll();
})();
