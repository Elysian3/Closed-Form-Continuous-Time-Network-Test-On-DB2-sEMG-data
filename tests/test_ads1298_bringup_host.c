#include <assert.h>
#include <setjmp.h>
#include <string.h>

#include "../firmware/diagnostics/ads1298_bringup/main/ads1298_bringup.c"

static uint8_t mock_registers[ADS_REG_CH1SET + ADS1298_CHANNEL_COUNT];
static bool mock_corrupt_config1_readback;
static bool mock_fail_readback;
static unsigned int mock_rdatac_count;
static unsigned int mock_start_high_count;
static int mock_bad_channel = -1;
static bool mock_no_drdy;
static unsigned int mock_control_polls;
static unsigned int mock_spi_calls;
static jmp_buf mock_task_exit;

esp_err_t spi_device_polling_transmit(spi_device_handle_t device, spi_transaction_t *transaction)
{
    const uint8_t *tx = transaction->tx_buffer;
    uint8_t *rx = transaction->rx_buffer;
    const size_t bytes = transaction->length / 8U;
    (void)device;
    mock_spi_calls++;

    if (bytes == 1U && tx[0] == ADS_CMD_RDATAC) {
        mock_rdatac_count++;
        return ESP_OK;
    }
    if ((tx[0] & 0xE0U) == ADS_CMD_WREG) {
        memcpy(&mock_registers[tx[0] & 0x1FU], &tx[2], tx[1] + 1U);
        return ESP_OK;
    }
    if ((tx[0] & 0xE0U) == ADS_CMD_RREG) {
        if (mock_fail_readback) {
            return ESP_FAIL;
        }
        memcpy(&rx[2], &mock_registers[tx[0] & 0x1FU], tx[1] + 1U);
        if (mock_corrupt_config1_readback && (tx[0] & 0x1FU) == ADS_REG_CONFIG1) {
            rx[2] ^= 1U;
        }
        if (mock_bad_channel >= 0 && (tx[0] & 0x1FU) == ADS_REG_CH1SET) {
            rx[2 + mock_bad_channel] ^= 1U;
        }
    }
    return ESP_OK;
}

esp_err_t gpio_set_level(gpio_num_t gpio, uint32_t level)
{
    if (gpio == ADS_START_GPIO && level != 0U) {
        mock_start_high_count++;
    }
    return ESP_OK;
}

esp_err_t gpio_intr_disable(gpio_num_t gpio) { (void)gpio; return ESP_OK; }
esp_err_t gpio_intr_enable(gpio_num_t gpio) { (void)gpio; return ESP_OK; }
void esp_rom_delay_us(uint32_t microseconds) { (void)microseconds; }
void vTaskDelay(TickType_t ticks) { (void)ticks; }
uint32_t ulTaskNotifyTake(BaseType_t clear, TickType_t wait) {
    (void)clear;
    if (mock_no_drdy) assert(wait != portMAX_DELAY);
    return 0U;
}
void port_stub_enter(portMUX_TYPE *lock) { (void)lock; }
void port_stub_exit(portMUX_TYPE *lock) { (void)lock; }

// Link the complete production translation unit; peripheral entry points not
// exercised here are inert. No copied reconfiguration/settling implementation.
void vTaskNotifyGiveFromISR(TaskHandle_t task, BaseType_t *woken) { (void)task; (void)woken; }
esp_err_t gpio_config(const gpio_config_t *config) { (void)config; return ESP_OK; }
esp_err_t gpio_install_isr_service(int flags) { (void)flags; return ESP_OK; }
esp_err_t gpio_isr_handler_add(gpio_num_t gpio, void (*handler)(void *), void *arg) {
    (void)gpio; (void)handler; (void)arg; return ESP_OK;
}
esp_err_t spi_bus_initialize(int host, const spi_bus_config_t *config, int dma) {
    (void)host; (void)config; (void)dma; return ESP_OK;
}
esp_err_t spi_bus_add_device(int host, const spi_device_interface_config_t *config, spi_device_handle_t *device) {
    (void)host; (void)config; (void)device; return ESP_OK;
}
QueueHandle_t xQueueCreate(size_t length, size_t item_size) { (void)length; (void)item_size; return (void *)1; }
BaseType_t xQueueSend(QueueHandle_t queue, const void *item, TickType_t wait) {
    (void)queue; (void)item; (void)wait; return pdTRUE;
}
BaseType_t xQueueReceive(QueueHandle_t queue, void *item, TickType_t wait) {
    (void)queue; (void)item; (void)wait;
    if (mock_no_drdy && ++mock_control_polls == 3U) longjmp(mock_task_exit, 1);
    return pdFALSE;
}
BaseType_t xQueuePeek(QueueHandle_t queue, void *item, TickType_t wait) {
    (void)queue; (void)item; (void)wait; return pdFALSE;
}
int usb_serial_jtag_write_bytes(const void *buffer, size_t length, TickType_t timeout) {
    (void)buffer; (void)timeout; return (int)length;
}
int usb_serial_jtag_read_bytes(void *buffer, size_t length, TickType_t timeout) {
    (void)buffer; (void)length; (void)timeout; return 0;
}
esp_err_t usb_serial_jtag_driver_install(const usb_serial_jtag_driver_config_t *config) {
    (void)config; return ESP_OK;
}
BaseType_t xTaskCreate(void (*task)(void *), const char *name, uint32_t stack, void *arg, unsigned int priority, TaskHandle_t *handle) {
    (void)task; (void)name; (void)stack; (void)arg; (void)priority; (void)handle; return pdPASS;
}

static void reset_mock(void)
{
    memset(mock_registers, 0, sizeof(mock_registers));
    memset(&s_counters, 0, sizeof(s_counters));
    s_sampling = (ads_sampling_config_t){ .rate_index = 2U, .pga_index = 4U,
        .config1 = ADS_CONFIG1_2KSPS_HR, .channel_set = ADS_CHSET_NORMAL_ELECTRODE,
        .pga_gain = 6U };
    s_settling_frames_remaining = 0U;
    mock_corrupt_config1_readback = false;
    mock_fail_readback = false;
    mock_rdatac_count = 0U;
    mock_start_high_count = 0U;
    mock_bad_channel = -1;
    mock_no_drdy = false;
    mock_control_polls = 0U;
    mock_spi_calls = 0U;
}

static ads_sampling_config_t requested_sampling(void)
{
    ads_sampling_config_t sampling;
    assert(ads_sampling_config_from_pc(1U, 5U, &sampling));
    return sampling;
}

static void test_verified_configuration_restarts_and_commits(void)
{
    reset_mock();
    const ads_sampling_config_t requested = requested_sampling();

    assert(ads_apply_sampling_config(&requested) == ESP_OK);
    assert(s_sampling.config1 == requested.config1);
    assert(s_sampling.channel_set == requested.channel_set);
    assert(s_settling_frames_remaining == ADS_SETTLING_FRAMES);
    assert(mock_rdatac_count == 1U);
    assert(mock_start_high_count == 1U);
}

static void test_bad_readback_does_not_restart_or_commit(void)
{
    reset_mock();
    const ads_sampling_config_t before = s_sampling;
    const ads_sampling_config_t requested = requested_sampling();
    mock_corrupt_config1_readback = true;

    assert(ads_apply_sampling_config(&requested) == ESP_ERR_INVALID_RESPONSE);
    assert(memcmp(&s_sampling, &before, sizeof(before)) == 0);
    assert(s_settling_frames_remaining == 0U);
    assert(mock_rdatac_count == 0U);
    assert(mock_start_high_count == 0U);
}

static void test_readback_spi_failure_does_not_restart_or_commit(void)
{
    reset_mock();
    const ads_sampling_config_t before = s_sampling;
    const ads_sampling_config_t requested = requested_sampling();
    mock_fail_readback = true;

    assert(ads_apply_sampling_config(&requested) == ESP_FAIL);
    assert(memcmp(&s_sampling, &before, sizeof(before)) == 0);
    assert(mock_rdatac_count == 0U);
    assert(mock_start_high_count == 0U);
}

static void test_all_settling_frames_are_discarded(void)
{
    reset_mock();
    s_settling_frames_remaining = ADS_SETTLING_FRAMES;

    assert(ads_discard_settling_frame());
    assert(ads_discard_settling_frame());
    assert(ads_discard_settling_frame());
    assert(!ads_discard_settling_frame());
    assert(s_counters.discarded_settling_frames == ADS_SETTLING_FRAMES);
}

int main(void)
{
    test_verified_configuration_restarts_and_commits();
    test_bad_readback_does_not_restart_or_commit();
    test_readback_spi_failure_does_not_restart_or_commit();
    test_all_settling_frames_are_discarded();
    for (int channel = 0; channel < ADS1298_CHANNEL_COUNT; channel++) {
        reset_mock();
        const ads_sampling_config_t requested = requested_sampling();
        mock_bad_channel = channel;
        assert(ads_apply_sampling_config(&requested) == ESP_ERR_INVALID_RESPONSE);
        assert(mock_start_high_count == 0U);
        // A subsequent good request can restart after failed readback.
        mock_bad_channel = -1;
        assert(ads_apply_sampling_config(&requested) == ESP_OK);
        assert(mock_start_high_count == 1U);
    }
    reset_mock();
    mock_no_drdy = true;
    if (setjmp(mock_task_exit) == 0) ads_acquisition_task(NULL);
    assert(mock_control_polls == 3U);
    assert(mock_spi_calls == 0U); // No fake sample is read on the control timeout.
    return 0;
}
