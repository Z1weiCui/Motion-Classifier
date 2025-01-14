import torch
import torch.nn as nn
import sys
import torch.nn.functional as F
import torchvision as torchvision
from CBAM import *
from helperfunction import *
from torchvision import transforms
from torchsummary import summary

num_classes = 1

# Hyper parameters
config = {"lr": 1e-3,
          "num_epochs": 80,
          "batch_size": 64,
          "regular_constant": 1e-5,
          # "transforms_train": transforms.Compose(transforms.Normalize([0.5], [0.5]))
          }

os.environ["KMP_DUPLICATE_LIB_OK"] = "True"
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


class SetData(torch.utils.data.Dataset):
    def __init__(self, data_root, data_label, transform=None, target_transform=None):
        self.data = data_root
        self.label = data_label
        self.transform = transform
        self.target_transform = target_transform

    def __getitem__(self, index):
        data = torch.from_numpy(self.data)[index]
        labels = torch.from_numpy(self.label)[index]
        return torch.unsqueeze(data.float(), 0), labels

    def __len__(self):
        return len(self.data)


def To_dataset(matrices, labels):
    source_data = matrices
    source_label = labels
    dataset = SetData(source_data, source_label)
    return dataset


def To_dataloader(dataset, size, validate=False):
    if validate:
        dataloader = torch.utils.data.DataLoader(
            dataset, batch_size=12, shuffle=False
        )

    else:
        if size < 12000:
            temp_batch = config["batch_size"] // 2
            print("Since the amount is small, batch_size becomes: ", temp_batch)
        elif size > 30000:
            temp_batch = config["batch_size"] * 2
            print("Since the amount is large, batch_size becomes: ", temp_batch)
        else:
            temp_batch = config["batch_size"]

        dataloader = torch.utils.data.DataLoader(
            dataset, batch_size=temp_batch, shuffle=True,
            drop_last=False
        )

    return dataloader


class Block(nn.Module):
    def __init__(self, in_channels, out_channels, down=True, act="relu", use_dropout=False):
        super(Block, self).__init__()
        # As mentioned in pix2pix paper, each conv block is composed of
        # conv - BN - relu/leakyRelu
        self.conv = nn.Sequential(
            # downsampling conv block.
            nn.Conv2d(in_channels, out_channels, kernel_size=4, stride=2, padding=1, bias=False, padding_mode="reflect")
            if down
            # upsampling conv block.
            else nn.ConvTranspose2d(in_channels, out_channels, kernel_size=4, stride=2, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU() if act == "relu" else nn.LeakyReLU(0.2),
        )

        self.use_dropout = use_dropout
        self.dropout = nn.Dropout(0.5)
        self.down = down

    def forward(self, x):
        x = self.conv(x)
        return self.dropout(x) if self.use_dropout else x


class MatClassificationNet(nn.Module):
    def __init__(self, num_classes=1, flag_cbam=False):
        super(MatClassificationNet, self).__init__()
        # output after initial layer: N * 3 * 3 * 3 -> N * 64 * 3 * 3
        self.flag_cbam = flag_cbam
        if flag_cbam:
            self.layer1 = nn.Sequential(
                nn.Conv2d(1, 64, kernel_size=3, padding=1, stride=1),
                nn.BatchNorm2d(64),
                nn.LeakyReLU(0.2)
            )

        else:
            self.layer1 = nn.Sequential(
                nn.Conv2d(1, 64, kernel_size=(3, 3)),
                nn.BatchNorm2d(64),
                nn.LeakyReLU()
            )
        # output after up1: N * 64 * 3 * 3 -> N * 32 * 6 * 6.
        self.up1 = Block(
            in_channels=64,
            out_channels=32,
            down=False,
            act='relu',
            use_dropout=True
        )

        self.cbam1 = CBAM(gate_channels=32)
        # output after up2: N * 32 * 6 * 6 -> N * 16 * 12 * 12.
        self.up2 = Block(
            in_channels=32,
            out_channels=16,
            down=False,
            act='relu',
            use_dropout=True
        )

        self.cbam2 = CBAM(gate_channels=16)

        # output after up3: N * 16 * 12 * 12 -> N * 8 * 24 * 24.
        self.up3 = Block(
            in_channels=16,
            out_channels=8,
            down=False,
            act='relu',
            use_dropout=False
        )

        self.cbam3 = CBAM(gate_channels=8)

        # output after up4: N * 8 * 24 * 24 -> N * 3 * 48 * 48.
        self.up4 = Block(
            in_channels=8,
            out_channels=3,
            down=False,
            act='relu',
            use_dropout=False
        )

        if self.flag_cbam:
            self.fc1 = nn.Sequential(
                nn.Linear(6912, 1024),
                nn.BatchNorm1d(1024),
                nn.LeakyReLU(0.2)
            )

            self.fc2 = nn.Sequential(
                nn.Linear(1024, 256),
                nn.BatchNorm1d(256),
                nn.LeakyReLU(0.2),
                nn.Dropout(0.3)
            )

            self.fc4 = nn.Sequential(
                nn.Linear(256, num_classes),
                nn.Sigmoid()
            )
        else:
            self.fc1 = nn.Sequential(
                nn.Linear(64, 128),
                nn.BatchNorm1d(128),
                nn.LeakyReLU(0.2)
            )

            self.fc2 = nn.Sequential(
                nn.Linear(128, 256),
                nn.BatchNorm1d(256),
                nn.LeakyReLU(0.2),
                nn.Dropout(0.3)
            )

            self.fc3 = nn.Sequential(
                nn.Linear(256, 512),
                nn.BatchNorm1d(512),
                nn.LeakyReLU(),
                nn.Dropout(0.2)
            )

            # self.fc4 = nn.Sequential(
            #     nn.Linear(512, num_classes), nn.LogSoftmax(dim=1)
            # )

            self.fc4 = nn.Sequential(
                nn.Linear(512, num_classes),
                nn.Sigmoid()
            )


    def forward(self, x):

        out = self.layer1(x)
        # print("out after layer1 is: ", out.size())

        if self.flag_cbam:
            out = self.up1(out)
            # print("out after up1 is: ", out.size())
            out = self.cbam1(out)
            # print("out after cbam1 is: ", out.size())
            out = self.up2(out)
            # print("out after up2 is: ", out.size())
            out = self.cbam2(out)
            # print("out after cbam2 is: ", out.size())
            out = self.up3(out)
            # print("out after up3 is: ", out.size())
            out = self.up4(out)


        # print("out after up4 is: ", out.size())
        out = torch.flatten(out, 1)
        # print("out type is: ", type(out))
        out = self.fc1(out)
        out = self.fc2(out)

        if not self.flag_cbam:
            out = self.fc3(out)

        out = self.fc4(out)

        return out


def train(train_dataloader, validation_dataloader, validation_size, video="", model=None, ROOT=os.getcwd()):
    if model is None:
        model = MatClassificationNet(num_classes).to(device)
    # loss_function = nn.CrossEntropyLoss()
    loss_function = nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config["lr"], weight_decay=config['regular_constant'])
    train_loss_value = []
    validate_loss_value = []
    train_accuracy = []
    validate_accuracy = []
    current_epoch = []
    acc = 0.0

    def lambda_rule(epoch):
        lr_l = 0.95 ** epoch
        return lr_l

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lambda_rule)

    total_step = len(train_dataloader)
    for epoch in range(config["num_epochs"]):
        current_epoch.append(epoch + 1)
        train_loss = 0
        train_correct = 0
        train_total = 0
        model.train()
        for batch_idx, (inputs, targets) in enumerate(train_dataloader):
            inputs, targets = inputs.to(device), targets.to(device)
            optimizer.zero_grad()
            outputs = model(inputs)

            outputs = outputs.squeeze().type(torch.float64)
            # print(outputs)
            # exit(-1)
            loss = loss_function(outputs, targets)
            loss.backward()
            optimizer.step()

            if (batch_idx + 1) % 100 == 0:
                print(
                    "Epoch [{}/{}], Step [{}/{}], Loss: {:.4f}".format(
                        epoch + 1,
                        config["num_epochs"],
                        batch_idx + 1,
                        total_step,
                        loss.item(),
                    )
                )
            train_loss += loss.item()
            train_total += targets.size(0)
            # print("outputs dimension:", outputs.size())
            # print("output:", outputs)
            # _, predicted = outputs.max(1)
            thresh = torch.tensor([0.5]).to(device)
            predicted = (outputs > thresh).float() * 1
            # print("predicted:", predicted)
            train_correct += predicted.eq(targets).sum().item()

        train_acc = 100.0 * (train_correct / train_total)
        train_loss /= train_total
        train_loss_value.append(train_loss)
        train_accuracy.append(train_acc)

        print("\nTraining set:  Accuracy: ----->  {:.0f}%\n".format(100.0 * (train_correct / train_total)))

        model.eval()
        validate_loss = 0
        correct = 0
        with torch.no_grad():
            for inputs, targets in validation_dataloader:
                inputs, targets = inputs.to(device), targets.to(device)
                targets = targets
                outputs = model(inputs).squeeze().type(torch.float64)
                thresh = torch.tensor([0.5]).to(device)
                predicted = (outputs > thresh).float() * 1
                correct += predicted.eq(targets).sum()
                validate_loss += loss_function(outputs, targets).item()

        validate_loss /= len(validation_dataloader.dataset)
        print("current lr:", scheduler.get_last_lr())
        print(
            "\nValidation set:  Accuracy: {}/{} ({:.0f}%)\n".format(
                correct, validation_size, 100.0 * correct / validation_size
            )
        )

        current_acc = 100.0 * correct / len(validation_dataloader.dataset)
        validate_loss_value.append(validate_loss)
        validate_accuracy.append(current_acc.item())

        if current_acc > acc:
            acc = current_acc
            torch.save(model.state_dict(), os.path.join(ROOT, "Runs", video, "ckpt.pth"))
            print("model save at checkpoint")
        else:
            scheduler.step()

    plt.figure()
    plt.plot(current_epoch, train_loss_value, "b", label="Training Loss")
    plt.plot(current_epoch, validate_loss_value, "r", label="Validation Loss")
    plt.title("Loss v.s. Epochs")
    plt.legend()
    plt.savefig(os.path.join(ROOT, "Runs", video, "loss_curve.jpg"))
    plt.close()

    plt.figure()
    plt.plot(current_epoch, train_accuracy, "b", label="Training Accuracy")
    plt.plot(current_epoch, validate_accuracy, "r", label="Validation Accuracy")
    plt.title("Accuracy v.s. Epochs")
    plt.legend()
    plt.savefig(os.path.join(ROOT, "Runs", video, "accuracy.jpg"))
    plt.close()
    return model
