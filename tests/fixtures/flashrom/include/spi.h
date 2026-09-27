#define JEDEC_RDID		0x9f
#define JEDEC_REMS		0x90
#define JEDEC_SFDP		0x5a
#define JEDEC_RES		0xab
#define JEDEC_WRSR		0x01
#define JEDEC_EWSR		0x50
#define JEDEC_READ		0x03
#define JEDEC_FAST_READ		0x0b /* with 8 cycles delay after sending address */
#define JEDEC_FAST_READ_DOUT	0x3b /* with 8 cycles delay and dual output */
#define JEDEC_BYTE_PROGRAM		0x02
#define JEDEC_AAI_WORD_PROGRAM			0xad
#define JEDEC_READ_4BA		0x13
