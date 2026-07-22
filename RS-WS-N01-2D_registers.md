## RS-WS-N01-2D-LCDIndustrial wall-mountedLCDTemperature andHumidityTransmitter UserManual(Type485)

## Registradores Modbus (RTU) - Sensor de Umidade/Temperatura

| Endereço (Hex) | Endereço (Dec) | Endereço PLC (Dec) | Conteúdo | Acesso |
|---|---|---|---|---|
| 0000H | 0 | 40001 | Umidade (valor real x10) | Leitura |
| 0001H | 1 | 40002 | Temperatura (valor real x10) | Leitura |
| 07D0H | 2000 | 42001 | Endereço do dispositivo | Leitura/Escrita |
| 07D1H | 2001 | 42002 | Baud rate do dispositivo | Leitura/Escrita |


## Parâmetros Básicos de Comunicação - Sensor Modbus

| Parâmetro | Valor |
|---|---|
| Código | Binário de 8 bits |
| Bit de dados | 8 bits |
| Bit de paridade | Sem paridade (none) |
| Bit de parada | 1 |
| Verificação de erro | CRC (Cyclic Redundancy Check) |
| Baud rate | 2400, 4800 ou 9600 bit/s (padrão de fábrica: 4800 bit/s) |


