const paymentStatusLabels = {
  pending: "Ожидает оплаты",
  paid: "Оплачено, активируется",
  succeeded: "Подписка активирована",
  failed: "Не удалось создать счёт",
};

function paymentStatusLabel(status) {
  return paymentStatusLabels[status] || status;
}

function formatPaymentAmount(amount, currency) {
  if (currency === "XTR") return amount + " ⭐";
  return (amount / 100).toLocaleString("ru-RU", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }) + " " + (currency === "RUB" ? "₽" : currency);
}
