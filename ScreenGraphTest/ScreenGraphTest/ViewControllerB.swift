//
//  ViewControllerB.swift
//  ScreenGraphTest
//
//  Created by Arnaud Coomans on 4/16/19.
//  Copyright © 2019 Arnaud Coomans. All rights reserved.
//

import UIKit

class ViewControllerB: UIViewController {
    
    @IBAction func buttonTapped(_ sender: Any) {
        let storyboard : UIStoryboard = UIStoryboard(name: "Main", bundle: nil)
        let viewController = storyboard.instantiateViewController(withIdentifier: "ViewControllerA")
        navigationController?.pushViewController(viewController, animated: true)
        
        event("button_a_tapped_event")
    }
    
}
